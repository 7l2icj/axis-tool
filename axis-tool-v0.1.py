import sys
import socket
import tkinter as tk
import yaml
import datetime
import os

HOST = "192.168.215.3"
PORT = 10101

def get_yaml_filepath():
    """
    コマンドライン引数を読み取り、指定があればそのパスを返す。
    なければ 'default_axis.yaml' を返す。
    """
    if len(sys.argv) > 1:
        return sys.argv[1]
    return "default_axis.yaml"

def load_config(yaml_file: str):
    """
    YAMLファイルを読み込み、group情報のリストを返す。
    例 (新しい形式):
    - group:
        name: debug
        axes:
        - axis:
            name: pmac_gonio_1_x
            display: pmac_gonio_x
            val2pulse: 2000
        - axis:
            ...
    """
    try:
        with open(yaml_file, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
    except FileNotFoundError:
        print(f"[Error] YAML file '{yaml_file}' not found.")
        sys.exit(1)
    except yaml.YAMLError as e:
        print(f"[Error] YAML parse error: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"[Error] Unexpected error reading YAML: {e}")
        sys.exit(1)

    groups = []
    if isinstance(data, list):
        for item in data:
            if "group" in item:
                group_dict = item["group"]
                group_name = group_dict.get("name")
                axes_config = group_dict.get("axes", [])
                axes_list = []
                if isinstance(axes_config, list):
                    for axis_item in axes_config:
                        if "axis" in axis_item:
                            axis_def = axis_item["axis"]
                            name = axis_def.get("name")
                            display = axis_def.get("display", name)
                            val2pulse = axis_def.get("val2pulse", 1000)
                            if name:
                                axes_list.append({
                                    "name": name,
                                    "display": display,
                                    "val2pulse": val2pulse
                                })
                if group_name:
                    groups.append({
                        "name": group_name,
                        "axes": axes_list
                    })
    return groups

def fetch_state_and_position(axis_name: str):
    """
    `get/bl_41in_{axis_name}/query` を送信し、(state, position) を返す。
    返答例: .../moving_12345pulse/0
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.connect((HOST, PORT))
        cmd = f"get/bl_41in_{axis_name}/query\n"
        print("[Send]", cmd.strip())
        s.sendall(cmd.encode("utf-8"))

        resp = s.recv(1024).decode("utf-8").strip()
        print("[Recv]", resp)

    splitted = resp.split('/')
    if len(splitted) > 3:
        part = splitted[3]  # e.g. "moving_12345pulse"
        if '_' in part:
            state, pos_str = part.split('_', 1)
            try:
                position = int(pos_str.replace("pulse", ""))
            except ValueError:
                position = 0
            return state, position
    return ("inactive", 0)

def put_position(axis_name: str, position: int) -> bool:
    """
    `put/bl_41in_{axis_name}/{position}pulse` を送信し、
    応答に "ok/0" が含まれれば成功
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.connect((HOST, PORT))
        cmd = f"put/bl_41in_{axis_name}/{position}pulse\n"
        print("[Send]", cmd.strip())
        s.sendall(cmd.encode("utf-8"))

        resp = s.recv(1024).decode("utf-8").strip()
        print("[Recv]", resp)
    return ("ok/0" in resp)

class AxisToolApp:
    def __init__(self, root, config_groups):
        self.root = root
        self.root.title("Axis Tool GUI")

        self.config_groups = config_groups

        # -------------------------------------------
        # 上部: group選択 + (pulse/mm)トグル を同じ行に
        # -------------------------------------------
        top_frame = tk.Frame(root)
        top_frame.pack(side=tk.TOP, fill=tk.X, padx=5, pady=5)

        # グループ選択
        self.group_var = tk.StringVar()
        group_names = [g["name"] for g in config_groups]
        self.group_var.set(group_names[0] if group_names else "N/A")

        self.option_group = tk.OptionMenu(
            top_frame, self.group_var, *group_names, command=self.on_group_changed
        )
        self.option_group.pack(side=tk.LEFT)

        # pulse/mm 切り替え
        self.unit_var = tk.StringVar(value="pulse")  # デフォルト: pulse

        tk.Radiobutton(
            top_frame, text="pulse", variable=self.unit_var, value="pulse",
            command=self.on_unit_changed
        ).pack(side=tk.LEFT, padx=5)

        tk.Radiobutton(
            top_frame, text="mm", variable=self.unit_var, value="mm",
            command=self.on_unit_changed
        ).pack(side=tk.LEFT, padx=5)

        # 「Update」ボタン
        btn_update = tk.Button(top_frame, text="Update", command=self.poll_all_axes)
        btn_update.pack(side=tk.LEFT, padx=5)

        # -------------------------------------------
        # 軸一覧表示フレーム
        # -------------------------------------------
        self.axis_frame = tk.Frame(root)
        self.axis_frame.pack(side=tk.TOP, fill=tk.BOTH, expand=True)

        # -------------------------------------------
        # コメント & Saveボタン
        # -------------------------------------------
        comment_frame = tk.Frame(root)
        comment_frame.pack(side=tk.TOP, fill=tk.X, padx=5, pady=5)
        tk.Label(comment_frame, text="Comment: ").pack(side=tk.LEFT)
        self.comment_text = tk.Entry(comment_frame, width=50)
        self.comment_text.pack(side=tk.LEFT, padx=5)

        btn_save = tk.Button(root, text="Save current value", command=self.on_save_button)
        btn_save.pack(side=tk.TOP, pady=5)

        # 内部管理
        self.axis_widgets = {}
        self.bg_default = {}

        # 最初のグループを表示
        if group_names:
            self.build_axes_for_group(group_names[0])

    # -----------------------------
    # イベントコールバック
    # -----------------------------
    def on_group_changed(self, new_group):
        """
        Group切り替え時:
          1) 軸リスト再構築
          2) Update(= poll_all_axes) 実行
        """
        self.build_axes_for_group(new_group)
        self.poll_all_axes()

    def on_unit_changed(self):
        """
        pulse/mm 切り替え時:
          1) 表示を更新(reformat)
          2) Update(= poll_all_axes) 実行
        """
        self.update_all_positions()
        self.poll_all_axes()

    def on_save_button(self):
        """
        1) update_all_positions() を実行
        2) LOG に書き出す
        """
        self.update_all_positions()
        self.save_current_value()

    # -----------------------------
    # GUI構築/更新メソッド
    # -----------------------------
    def build_axes_for_group(self, group_name):
        # 既存ウィジェットクリア
        for child in self.axis_frame.winfo_children():
            child.destroy()
        self.axis_widgets.clear()
        self.bg_default.clear()

        # 対象グループ情報
        group_info = None
        for g in self.config_groups:
            if g["name"] == group_name:
                group_info = g
                break
        if not group_info:
            return

        for axis_def in group_info.get("axes", []):
            axis_name = axis_def["name"]
            axis_display = axis_def.get("display", axis_name)
            val2pulse = axis_def.get("val2pulse", 1000)

            row_frame = tk.Frame(self.axis_frame)
            row_frame.pack(side=tk.TOP, fill=tk.X, padx=5, pady=3)

            tk.Label(row_frame, text=axis_display, width=20, anchor="w").pack(side=tk.LEFT)

            pos_var = tk.StringVar(value="---")
            lbl_pos = tk.Label(row_frame, textvariable=pos_var, width=15, anchor="e")
            lbl_pos.pack(side=tk.LEFT, padx=(5, 10))

            self.bg_default[axis_name] = lbl_pos.cget("bg")

            entry_var = tk.StringVar()
            ent = tk.Entry(row_frame, textvariable=entry_var, width=8)
            ent.pack(side=tk.LEFT, padx=(0, 5))

            # move -> abs
            btn_abs = tk.Button(row_frame, text="abs",
                                command=lambda ax=axis_name: self.abs_axis(ax))
            btn_abs.pack(side=tk.LEFT, padx=2)

            btn_plus = tk.Button(row_frame, text="+",
                                 command=lambda ax=axis_name: self.plus_axis(ax))
            btn_plus.pack(side=tk.LEFT, padx=2)

            btn_minus = tk.Button(row_frame, text="-",
                                  command=lambda ax=axis_name: self.minus_axis(ax))
            btn_minus.pack(side=tk.LEFT, padx=2)

            self.axis_widgets[axis_name] = {
                "pos_var": pos_var,
                "pos_label": lbl_pos,
                "entry_var": entry_var,
                "val2pulse": val2pulse
            }

    def update_all_positions(self):
        """
        unit_var (pulse/mm) に応じて既存のpos_varを再フォーマット
        """
        for axis_name, wdict in self.axis_widgets.items():
            text_now = wdict["pos_var"].get()
            numeric_val = 0
            is_valid = False
            if text_now and text_now not in ("---", ""):
                try:
                    tmp = text_now.replace("pulse", "").replace("mm", "")
                    numeric_val = float(tmp)
                    is_valid = True
                except ValueError:
                    pass

            if is_valid:
                if self.unit_var.get() == "pulse":
                    new_str = f"{int(numeric_val)}pulse"
                else:
                    mm_val = numeric_val / wdict["val2pulse"]
                    new_str = f"{mm_val:.3f}mm"
                wdict["pos_var"].set(new_str)

    def poll_all_axes(self):
        """
        全軸について "Update" 相当: state/posを問い合わせ、movingなら連続ポーリング
        """
        group_name = self.group_var.get()
        group_axes = self.get_axes_in_group(group_name)
        for axis_def in group_axes:
            axis_name = axis_def["name"]
            self.poll_axis(axis_name)

    def poll_axis(self, axis_name: str):
        """
        1回 state/pos を取得 → ラベル更新。
        state != inactive なら ラベルを黄色に → 1秒後再ポーリング
        """
        state, position = fetch_state_and_position(axis_name)
        self.update_position_label(axis_name, position)

        pos_label = self.axis_widgets[axis_name]["pos_label"]
        if state.lower() != "inactive":
            pos_label.config(bg="yellow")
            self.root.after(1000, lambda: self.poll_axis(axis_name))
        else:
            pos_label.config(bg=self.bg_default[axis_name])

    def update_position_label(self, axis_name: str, pos_int: int):
        """
        pos_int(pulse) を unit_var に合わせて表示
        """
        if axis_name not in self.axis_widgets:
            return
        wdict = self.axis_widgets[axis_name]
        val2pulse = wdict["val2pulse"]
        if self.unit_var.get() == "pulse":
            text = f"{pos_int}pulse"
        else:
            mm_val = pos_int / val2pulse
            text = f"{mm_val:.3f}mm"
        wdict["pos_var"].set(text)

    # -----------------------------
    # ボタン操作 (abs, +, -)
    # -----------------------------
    def abs_axis(self, axis_name: str):
        """
        Entry に入力された値を絶対位置 (pulse) として put
        成功したら poll_axis
        """
        wdict = self.axis_widgets.get(axis_name)
        if not wdict:
            return
        val_str = wdict["entry_var"].get().strip()
        if not val_str:
            return

        try:
            in_val = float(val_str)
        except ValueError:
            return

        if self.unit_var.get() == "mm":
            in_val = int(round(in_val * wdict["val2pulse"]))
        else:
            in_val = int(round(in_val))

        success = put_position(axis_name, in_val)
        if success:
            self.poll_axis(axis_name)
        else:
            print(f"[Error] abs(move) command failed for {axis_name}")

    def plus_axis(self, axis_name: str):
        wdict = self.axis_widgets.get(axis_name)
        if not wdict:
            return
        val_str = wdict["entry_var"].get().strip()
        if not val_str:
            return

        try:
            in_val = float(val_str)
        except ValueError:
            return

        state, current_pulse = fetch_state_and_position(axis_name)
        if self.unit_var.get() == "mm":
            in_val = int(round(in_val * wdict["val2pulse"]))
        else:
            in_val = int(round(in_val))

        new_pos = current_pulse + in_val
        success = put_position(axis_name, new_pos)
        if success:
            self.poll_axis(axis_name)
        else:
            print(f"[Error] Plus command failed for {axis_name}")

    def minus_axis(self, axis_name: str):
        wdict = self.axis_widgets.get(axis_name)
        if not wdict:
            return
        val_str = wdict["entry_var"].get().strip()
        if not val_str:
            return

        try:
            in_val = float(val_str)
        except ValueError:
            return

        state, current_pulse = fetch_state_and_position(axis_name)
        if self.unit_var.get() == "mm":
            in_val = int(round(in_val * wdict["val2pulse"]))
        else:
            in_val = int(round(in_val))

        new_pos = current_pulse - in_val
        success = put_position(axis_name, new_pos)
        if success:
            self.poll_axis(axis_name)
        else:
            print(f"[Error] Minus command failed for {axis_name}")

    # -----------------------------
    # 保存 (ログ) 関連
    # -----------------------------
    def get_axes_in_group(self, group_name: str):
        for g in self.config_groups:
            if g["name"] == group_name:
                return g["axes"]
        return []

    def save_current_value(self):
        """
        ログ出力:
        - log:
            time: {YYYY/MM/DD HH:MM:SS}
            group: {group}
            comment: "{comment}"
            axis:
              - axis_name: 12345 pulse
              - ...
        """
        group_name = self.group_var.get()
        comment = self.comment_text.get().strip()
        now_str = datetime.datetime.now().strftime("%Y/%m/%d %H:%M:%S")
        today_str = datetime.datetime.now().strftime("%Y%m%d")
        filename = f"{today_str}_{group_name}.yaml"

        axes_list = self.get_axes_in_group(group_name)
        axis_lines = []
        for axis_def in axes_list:
            axis_name = axis_def["name"]
            _state, position_pulse = fetch_state_and_position(axis_name)
            axis_lines.append(f"      - {axis_name}: {position_pulse} pulse")

        log_text = []
        log_text.append("- log:")
        log_text.append(f"    time: {now_str}")
        log_text.append(f"    group: {group_name}")
        log_text.append(f"    comment: \"{comment}\"")
        log_text.append("    axis:")
        log_text.extend(axis_lines)
        log_text.append("")

        mode = "a" if os.path.exists(filename) else "w"
        with open(filename, mode, encoding="utf-8") as f:
            for line in log_text:
                f.write(line + "\n")

        print(f'[Info] Appended current values to "{filename}"')

def main():
    yaml_file = get_yaml_filepath()
    config_groups = load_config(yaml_file)
    if not config_groups:
        print("[Warn] No groups found in config. Exiting.")
        sys.exit(1)

    root = tk.Tk()
    app = AxisToolApp(root, config_groups)
    root.mainloop()

if __name__ == "__main__":
    main()

