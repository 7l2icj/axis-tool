import sys
import socket
import tkinter as tk
import yaml
import datetime
import os
import time

HOST = "192.168.215.3"
PORT = 10101

BSS_CONFIG_PATH = "/blconfig/bss/bss.config"

def parse_bss_config(config_path: str):
    """
    /blconfig/bss/bss.config を解析し、以下の形式のリストを返す:
    [
      {"name": axis_name, "display": axis_display, "val2pulse": val2pulse},
      ...
    ]
    - _axis_name: -> name
    - _axis_comment: -> display (カンマ区切りの先頭)
    - _val2pulse: -> val2pulse (int)
    """
    if not os.path.exists(config_path):
        print(f"[Warn] bss.config not found: {config_path}")
        return []
    with open(config_path, "r", encoding="utf-8") as f:
        lines = f.readlines()

    blocks = []
    current_block = []
    for line in lines:
        s = line.strip()
        if s.startswith("_axis_begin"):
            current_block = [s]
        elif s.startswith("_axis_end"):
            current_block.append(s)
            blocks.append(current_block)
            current_block = []
        else:
            if current_block:
                current_block.append(s)

    result = []
    for blk in blocks:
        axis_name = None
        axis_comment = ""
        val2pulse = 1000
        for l in blk:
            if l.startswith("_axis_name:"):
                axis_name = l.split(":", 1)[1].strip()
            elif l.startswith("_axis_comment:"):
                axis_comment = l.split(":", 1)[1].strip()
            elif l.startswith("_val2pulse:"):
                tmp = l.split(":", 1)[1].strip()
                try:
                    val2pulse = int(tmp)
                except ValueError:
                    pass
        if axis_name:
            display = axis_comment.split(",", 1)[0].strip() if axis_comment else axis_name
            result.append({
                "name": axis_name,
                "display": display,
                "val2pulse": val2pulse
            })
    return result

def get_yaml_filepath():
    """
    コマンドライン引数にYAMLファイルがあればそれを返し、
    なければ default_axis.yaml を返す
    """
    if len(sys.argv) > 1:
        return sys.argv[1]
    return "default_axis.yaml"

def load_config(yaml_file: str):
    """
    YAMLファイルを読み込み、groupのリストを返す
    [
      {
        "name": group_name,
        "axes": [
          {"name": ..., "display": ..., "val2pulse": ...},
          ...
        ]
      },
      ...
    ]
    
    修正点:
      - YAMLから読み込んだ各軸の name, display はそのまま使用し、
        bss.config から同名の軸があれば、その中の val2pulse 等のパラメータを取得する
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

    # bss.config から全軸情報を読み込み、軸名をキーとする辞書にする
    bss_axes = parse_bss_config(BSS_CONFIG_PATH)
    bss_axes_map = {axis["name"]: axis for axis in bss_axes}

    groups = []
    if isinstance(data, list):
        for item in data:
            if "group" in item:
                grp = item["group"]
                grp_name = grp.get("name")
                axes_conf = grp.get("axes", [])
                axes_list = []
                if isinstance(axes_conf, list):
                    for ax in axes_conf:
                        if "axis" in ax:
                            adef = ax["axis"]
                            aname = adef.get("name")
                            # YAMLで定義された display を優先、なければ name を使う
                            adisplay = adef.get("display", aname)
                            # bss.config で同じ名前の軸があれば、そちらからパラメータを取得
                            if aname in bss_axes_map:
                                aval2pulse = bss_axes_map[aname].get("val2pulse", 1000)
                            else:
                                aval2pulse = 1000
                            if aname:
                                axes_list.append({
                                    "name": aname,
                                    "display": adisplay,
                                    "val2pulse": aval2pulse
                                })
                if grp_name:
                    groups.append({
                        "name": grp_name,
                        "axes": axes_list
                    })
    return groups

def fetch_state_and_position(axis_name: str):
    """
    "get/bl_41in_{axis_name}/query" を送信
    応答例: .../{state}_{pos}pulse/0
    -> (state, pos_int)
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
        part = splitted[3]
        if '_' in part:
            st, pos_str = part.split('_', 1)
            try:
                pos_int = int(pos_str.replace("pulse", "").strip())
            except ValueError:
                pos_int = 0
            return (st, pos_int)
    return ("inactive", 0)

def put_position(axis_name: str, position: int) -> bool:
    """
    "put/bl_41in_{axis_name}/{position}pulse" を送信
    -> "ok/0" が返ればTrue
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.connect((HOST, PORT))
        cmd = f"put/bl_41in_{axis_name}/{position}pulse\n"
        print("[Send]", cmd.strip())
        s.sendall(cmd.encode("utf-8"))

        resp = s.recv(1024).decode("utf-8").strip()
        print("[Recv]", resp)
    return ("ok/0" in resp)

def put_stop(axis_name: str) -> bool:
    """
    "put/bl_41in_{axis_name}/stop" を送信
    -> "ok/0" が返ればTrue
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.connect((HOST, PORT))
        cmd = f"put/bl_41in_{axis_name}/stop\n"
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
        # お気に入り: 先頭が最新, 最大10軸など
        self.favorite_list = []

        # ---------- 上部バー ----------
        top_frame = tk.Frame(root)
        top_frame.pack(side=tk.TOP, fill=tk.X, padx=5, pady=5)

        self.group_var = tk.StringVar()
        group_names = [g["name"] for g in config_groups]
        if group_names:
            self.group_var.set(group_names[0])

        self.option_group = tk.OptionMenu(
            top_frame, self.group_var, *group_names, command=self.on_group_changed
        )
        self.option_group.pack(side=tk.LEFT)

        self.unit_var = tk.StringVar(value="pulse")
        tk.Radiobutton(top_frame, text="pulse", variable=self.unit_var, value="pulse",
                       command=self.on_unit_changed).pack(side=tk.LEFT, padx=5)
        tk.Radiobutton(top_frame, text="mm", variable=self.unit_var, value="mm",
                       command=self.on_unit_changed).pack(side=tk.LEFT, padx=5)

        btn_update = tk.Button(top_frame, text="Update", command=self.poll_all_axes)
        btn_update.pack(side=tk.LEFT, padx=5)

        # ---------- 中央 (スクロール対応) ----------
        self.main_frame = tk.Frame(root)
        self.main_frame.pack(side=tk.TOP, fill=tk.BOTH, expand=True)

        self.canvas = tk.Canvas(self.main_frame)
        self.canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        self.scrollbar = tk.Scrollbar(self.main_frame, orient=tk.VERTICAL,
                                      command=self.canvas.yview)
        self.scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        self.canvas.configure(yscrollcommand=self.scrollbar.set)

        self.inner_frame = tk.Frame(self.canvas)
        self.canvas.create_window((0, 0), window=self.inner_frame, anchor="nw")

        self.inner_frame.bind("<Configure>", self.on_inner_frame_configure)

        # ---------- 下部 (コメント & Save) ----------
        comment_frame = tk.Frame(root)
        comment_frame.pack(side=tk.TOP, fill=tk.X, padx=5, pady=5)
        tk.Label(comment_frame, text="Comment: ").pack(side=tk.LEFT)
        self.comment_text = tk.Entry(comment_frame, width=50)
        self.comment_text.pack(side=tk.LEFT, padx=5)

        btn_save = tk.Button(root, text="Save current value", command=self.on_save_button)
        btn_save.pack(side=tk.TOP, pady=5)

        # 内部管理
        self.axis_widgets = {}  # 軸ごとの {pos_var, pos_label, entry_var, val2pulse, ...}
        self.bg_default = {}    # 軸ごとのデフォルト背景色

        # 初期グループ表示
        if group_names:
            self.build_axes_for_group(group_names[0])

    # ---------- スクロール更新 ----------
    def on_inner_frame_configure(self, event):
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))

    # ---------- イベント ----------
    def on_group_changed(self, new_group):
        self.build_axes_for_group(new_group)
        self.poll_all_axes()

    def on_unit_changed(self):
        self.update_all_positions()
        self.poll_all_axes()

    def on_save_button(self):
        self.update_all_positions()
        self.save_current_value()

    # ---------- グループ表示 ----------
    def build_axes_for_group(self, group_name):
        # 既存ウィジェット消去
        for child in self.inner_frame.winfo_children():
            child.destroy()
        self.axis_widgets.clear()
        self.bg_default.clear()

        # group_info 取得
        group_info = None
        for g in self.config_groups:
            if g["name"] == group_name:
                group_info = g
                break
        if not group_info:
            return

        axes_list = []
        if group_name == "favorite":
            axes_list = self.favorite_list
        else:
            axes_list = group_info.get("axes", [])

        for axis_def in axes_list:
            axis_name = axis_def["name"]
            axis_disp = axis_def.get("display", axis_name)
            val2pulse = axis_def.get("val2pulse", 1000)

            row_frame = tk.Frame(self.inner_frame)
            row_frame.pack(side=tk.TOP, fill=tk.X, padx=5, pady=3)

            # 軸名ラベル (ダブルクリックで favorite)
            lbl_axis = tk.Label(row_frame, text=axis_disp, width=20, anchor="w")
            lbl_axis.pack(side=tk.LEFT)
            lbl_axis.bind("<Double-Button-1>", 
                          lambda e, n=axis_name, d=axis_disp, v=val2pulse:
                              self.toggle_favorite(n, d, v))

            # Position表示
            pos_var = tk.StringVar(value="---")
            lbl_pos = tk.Label(row_frame, textvariable=pos_var, width=15, anchor="e")
            lbl_pos.pack(side=tk.LEFT, padx=(5, 10))

            self.bg_default[axis_name] = lbl_pos.cget("bg")

            # Entry
            entry_var = tk.StringVar()
            ent = tk.Entry(row_frame, textvariable=entry_var, width=8)
            ent.pack(side=tk.LEFT, padx=(0, 5))

            # absボタン
            btn_abs = tk.Button(row_frame, text="abs",
                                command=lambda n=axis_name,d=axis_disp,v=val2pulse:
                                    self.abs_axis(n, d, v))
            btn_abs.pack(side=tk.LEFT, padx=2)

            # +ボタン
            btn_plus = tk.Button(row_frame, text="+",
                                 command=lambda n=axis_name,d=axis_disp,v=val2pulse:
                                     self.plus_axis(n, d, v))
            btn_plus.pack(side=tk.LEFT, padx=2)

            # -ボタン
            btn_minus = tk.Button(row_frame, text="-",
                                  command=lambda n=axis_name,d=axis_disp,v=val2pulse:
                                      self.minus_axis(n, d, v))
            btn_minus.pack(side=tk.LEFT, padx=2)

            # stopボタン
            btn_stop = tk.Button(row_frame, text="stop",
                                 command=lambda n=axis_name: self.stop_axis(n))
            btn_stop.pack(side=tk.LEFT, padx=2)

            # favorite なら軸名ラベルを薄水色
            if self.is_in_favorite(axis_name):
                lbl_axis.config(bg="lightblue")

            # 登録
            self.axis_widgets[axis_name] = {
                "pos_var": pos_var,
                "pos_label": lbl_pos,
                "entry_var": entry_var,
                "val2pulse": val2pulse,
                "display": axis_disp,
                "axis_label": lbl_axis
            }

        # スクロール領域再設定
        self.inner_frame.update_idletasks()
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))

    # ---------- Favorite管理 ----------
    def is_in_favorite(self, axis_name: str) -> bool:
        return any(a["name"] == axis_name for a in self.favorite_list)

    def toggle_favorite(self, axis_name: str, axis_display: str, val2pulse: int):
        """
        ダブルクリック時に追加/削除トグル
        """
        if self.is_in_favorite(axis_name):
            # 削除
            self.favorite_list = [a for a in self.favorite_list if a["name"] != axis_name]
        else:
            # 先頭に追加 (重複除去)
            self.favorite_list = [a for a in self.favorite_list if a["name"] != axis_name]
            self.favorite_list.insert(0, {
                "name": axis_name,
                "display": axis_display,
                "val2pulse": val2pulse
            })
            # 最大10
            if len(self.favorite_list) > 10:
                self.favorite_list = self.favorite_list[:10]

        # 同期
        self.sync_favorite_group()

        # 軸名ラベルの色変え
        if axis_name in self.axis_widgets:
            if self.is_in_favorite(axis_name):
                self.axis_widgets[axis_name]["axis_label"].config(bg="lightblue")
            else:
                self.axis_widgets[axis_name]["axis_label"].config(bg=self.bg_default[axis_name])

        # favorite グループなら再描画
        if self.group_var.get() == "favorite":
            self.build_axes_for_group("favorite")

    def add_to_favorite_on_move(self, axis_name: str, axis_display: str, val2pulse: int):
        """
        abs/+/- 移動成功時に先頭追加
        """
        self.favorite_list = [a for a in self.favorite_list if a["name"] != axis_name]
        self.favorite_list.insert(0, {
            "name": axis_name,
            "display": axis_display,
            "val2pulse": val2pulse
        })
        if len(self.favorite_list) > 10:
            self.favorite_list = self.favorite_list[:10]
        self.sync_favorite_group()

        if axis_name in self.axis_widgets:
            self.axis_widgets[axis_name]["axis_label"].config(bg="lightblue")

    def sync_favorite_group(self):
        for g in self.config_groups:
            if g["name"] == "favorite":
                g["axes"] = self.favorite_list
                break

    # ---------- 移動操作 ----------
    def abs_axis(self, axis_name: str, axis_display: str, val2pulse: int):
        w = self.axis_widgets.get(axis_name)
        if not w:
            return
        val_str = w["entry_var"].get().strip()
        if not val_str:
            return
        try:
            fv = float(val_str)
        except ValueError:
            return

        if self.unit_var.get() == "mm":
            pos = int(round(fv * val2pulse))
        else:
            pos = int(round(fv))

        if put_position(axis_name, pos):
            self.poll_axis(axis_name)
            self.add_to_favorite_on_move(axis_name, axis_display, val2pulse)
        else:
            print(f"[Error] abs command failed for {axis_name}")

    def plus_axis(self, axis_name: str, axis_display: str, val2pulse: int):
        w = self.axis_widgets.get(axis_name)
        if not w:
            return
        val_str = w["entry_var"].get().strip()
        if not val_str:
            return
        try:
            fv = float(val_str)
        except ValueError:
            return

        st, cur_pulse = fetch_state_and_position(axis_name)
        if self.unit_var.get() == "mm":
            diff = int(round(fv * val2pulse))
        else:
            diff = int(round(fv))

        new_pos = cur_pulse + diff
        if put_position(axis_name, new_pos):
            self.poll_axis(axis_name)
            self.add_to_favorite_on_move(axis_name, axis_display, val2pulse)
        else:
            print(f"[Error] plus command failed for {axis_name}")

    def minus_axis(self, axis_name: str, axis_display: str, val2pulse: int):
        w = self.axis_widgets.get(axis_name)
        if not w:
            return
        val_str = w["entry_var"].get().strip()
        if not val_str:
            return
        try:
            fv = float(val_str)
        except ValueError:
            return

        st, cur_pulse = fetch_state_and_position(axis_name)
        if self.unit_var.get() == "mm":
            diff = int(round(fv * val2pulse))
        else:
            diff = int(round(fv))

        new_pos = cur_pulse - diff
        if put_position(axis_name, new_pos):
            self.poll_axis(axis_name)
            self.add_to_favorite_on_move(axis_name, axis_display, val2pulse)
        else:
            print(f"[Error] minus command failed for {axis_name}")

    def stop_axis(self, axis_name: str):
        """
        stopボタン: "put/bl_41in_{axis_name}/stop"
        """
        ok = put_stop(axis_name)
        if ok:
            print(f"[Info] Stopped axis: {axis_name}")
        else:
            print(f"[Error] stop command failed for {axis_name}")

    # ---------- ポーリング ----------
    def poll_all_axes(self):
        grp = self.group_var.get()
        group_info = None
        for g in self.config_groups:
            if g["name"] == grp:
                group_info = g
                break
        if not group_info:
            return

        axes_list = []
        if grp == "favorite":
            axes_list = self.favorite_list
        else:
            axes_list = group_info.get("axes", [])

        for ad in axes_list:
            self.poll_axis(ad["name"])

    def poll_axis(self, axis_name: str):
        """
        1回問い合わせ:
          - moving -> 背景yellow, 1秒後再poll
          - inactive -> favoriteなら薄水色, でなければ標準色
        """
        if axis_name not in self.axis_widgets:
            return
        st, pos_int = fetch_state_and_position(axis_name)

        w = self.axis_widgets[axis_name]
        # pos_var更新
        if self.unit_var.get() == "pulse":
            w["pos_var"].set(f"{pos_int} pulse")
        else:
            mm_val = pos_int / w["val2pulse"]
            w["pos_var"].set(f"{mm_val:.3f} mm")

        # 背景
        lbl_pos = w["pos_label"]
        if st.lower() != "inactive":
            lbl_pos.config(bg="yellow")
            self.root.after(1000, lambda: self.poll_axis(axis_name))
        else:
            # inactive
            lbl_pos.config(bg=self.bg_default[axis_name])

    # ---------- 表示更新 ----------
    def update_all_positions(self):
        for axis_name, wdict in self.axis_widgets.items():
            txt = wdict["pos_var"].get()
            if not txt or txt in ("---", ""):
                continue
            # "1234 pulse" / "12.345 mm"
            tmp = txt.replace("pulse", "").replace("mm", "").strip()
            try:
                fval = float(tmp)
            except ValueError:
                continue

            if self.unit_var.get() == "pulse":
                wdict["pos_var"].set(f"{int(fval)} pulse")
            else:
                mm_val = fval / wdict["val2pulse"]
                wdict["pos_var"].set(f"{mm_val:.3f} mm")

    # ---------- ログ保存 ----------
    def save_current_value(self):
        grp = self.group_var.get()
        cmt = self.comment_text.get().strip()
        now_str = datetime.datetime.now().strftime("%Y/%m/%d %H:%M:%S")
        date_str = datetime.datetime.now().strftime("%Y%m%d")
        filename = f"{date_str}_{grp}.yaml"

        # グループのaxes
        group_info = None
        for g in self.config_groups:
            if g["name"] == grp:
                group_info = g
                break
        if not group_info:
            return

        axes_list = []
        if grp == "favorite":
            axes_list = self.favorite_list
        else:
            axes_list = group_info.get("axes", [])

        # 最新値を取得
        lines_axis = []
        for ax in axes_list:
            an = ax["name"]
            st, pos_pulse = fetch_state_and_position(an)
            time.sleep(0.1)
            lines_axis.append(f"      - {an}: {pos_pulse} pulse")

        lines = []
        lines.append("- log:")
        lines.append(f"    time: {now_str}")
        lines.append(f"    group: {grp}")
        lines.append(f"    comment: \"{cmt}\"")
        lines.append("    axis:")
        lines.extend(lines_axis)
        lines.append("")

        mode = "a" if os.path.exists(filename) else "w"
        with open(filename, mode, encoding="utf-8") as f:
            for ln in lines:
                f.write(ln + "\n")

        print(f"[Info] Appended current values to '{filename}'")


def main():
    # 1) YAML読み込み
    yaml_file = get_yaml_filepath()
    config_groups = load_config(yaml_file)

    # 2) bss.config -> "all" グループ
    all_axes = parse_bss_config(BSS_CONFIG_PATH)
    config_groups.append({"name": "all", "axes": all_axes})

    # 3) "favorite" グループ (初期空)
    config_groups.append({"name": "favorite", "axes": []})

    # 4) GUI起動
    root = tk.Tk()
    app = AxisToolApp(root, config_groups)
    root.mainloop()

if __name__ == "__main__":
    main()

