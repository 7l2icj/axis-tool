#!/usr/bin/env python
import sys
import socket
import tkinter as tk
import yaml
import datetime
import os
import time
from tkinter import simpledialog, messagebox

HOST = "10.178.215.3"
PORT = 10101

BSS_CONFIG_PATH = "/blconfig/bss/bss.config"


# --- Axisクラス ---
class Axis:
    def __init__(self, axis_name: str, display: str = None, val2pulse: int = 1000, sense: int = 1, unit: str = "pulse"):
        self.axis_name = axis_name
        self.display = display if display is not None else axis_name
        self.val2pulse = val2pulse
        self.sense = sense
        self.unit = unit

    def copy(self):
        return Axis(self.axis_name, self.display, self.val2pulse, self.sense, self.unit)

    def __repr__(self):
        return f"Axis({self.axis_name}, {self.display}, {self.val2pulse}, {self.sense}, {self.unit})"


# --- bss.config 解析 ---
def parse_bss_config(config_path: str):
    """
    /blconfig/bss/bss.config を解析し、Axisオブジェクトのリストを返す:
      Axis(axis_name, display, val2pulse, sense, unit="pulse")
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
        sense = 1  # デフォルトは 1
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
            elif l.startswith("_sense:"):
                tmp = l.split(":", 1)[1].strip()
                try:
                    sense = int(tmp)
                    if sense not in (1, -1):
                        sense = 1
                except ValueError:
                    pass
        if axis_name:
            display = axis_comment.split(",", 1)[0].strip() if axis_comment else axis_name
            result.append(Axis(axis_name, display, val2pulse, sense, unit="pulse"))
    return result


# --- 通信用関数 ---
def fetch_state_and_position(axis: Axis):
    """
    "get/bl_41in_{axis.axis_name}/query" などを送信し、
    応答から (state, position) を返す
    """
    axis_name = axis.axis_name
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.connect((HOST, PORT))
        if axis.unit == "mm" or axis_name.endswith("width") or axis_name.endswith("height") or axis_name.endswith("vertical") or axis_name.endswith("horizontal"):
        
            cmd = f"get/bl_41in_{axis_name}/query\n"
            print("[Send]", cmd.strip())
            s.sendall(cmd.encode("utf-8"))
            resp = s.recv(1024).decode("utf-8").strip()
            print("[Recv]", resp)
            splitted = resp.split('/')
            if len(splitted) > 3:
                st = splitted[3]
                if st == "ok":
                    st = "inactive"
            if axis_name.endswith("width") or axis_name.endswith("height"):
                cmd = f"get/bl_41in_{axis_name}/aperture\n"
            else:
                cmd = f"get/bl_41in_{axis_name}/position\n"
            print("[Send]", cmd.strip())
            s.sendall(cmd.encode("utf-8"))
            resp = s.recv(1024).decode("utf-8").strip()
            print("[Recv]", resp)
            splitted = resp.split('/')
            if len(splitted) > 3:
                part = splitted[3]
                if 'mm' in part:
                    try:
                        pos = float(part.replace("mm", "").strip())
                    except ValueError:
                        pos = 0
                    axis.unit = "mm"
                    return (st, pos)
        else:
            cmd = f"get/bl_41in_{axis_name}/query\n"
            print("[Send]", cmd.strip())
            s.sendall(cmd.encode("utf-8"))
            resp = s.recv(1024).decode("utf-8").strip()
            print("[Recv]", resp)
    splitted = resp.split('/')
    if len(splitted) > 3:
        part = splitted[3]
        if part == "ok":
            axis.unit = "mm"
            return fetch_state_and_position(axis)
        if '_' in part:
            st, pos_str = part.split('_', 1)
            try:
                pos_int = int(pos_str.replace("pulse", "").strip())
            except ValueError:
                pos_int = 0
            return (st, pos_int)
    return ("inactive", 0)


def put_position(axis: Axis, position: int) -> bool:
    """
    "put/bl_41in_{axis.axis_name}/{position}pulse" を送信し、
    応答が "ok/0" なら True を返す
    """
    axis_name = axis.axis_name
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.connect((HOST, PORT))
        cmd = f"put/bl_41in_{axis_name}/{position}pulse\n"
        print("[Send]", cmd.strip())
        s.sendall(cmd.encode("utf-8"))
        resp = s.recv(1024).decode("utf-8").strip()
        print("[Recv]", resp)
    return ("ok/0" in resp)


def put_stop(axis: Axis) -> bool:
    """
    "put/bl_41in_{axis.axis_name}/stop" を送信し、
    応答が "ok/0" なら True を返す
    """
    axis_name = axis.axis_name
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.connect((HOST, PORT))
        cmd = f"put/bl_41in_{axis_name}/stop\n"
        print("[Send]", cmd.strip())
        s.sendall(cmd.encode("utf-8"))
        resp = s.recv(1024).decode("utf-8").strip()
        print("[Recv]", resp)
    return ("ok/0" in resp)


# --- YAML設定読み込み ---
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
    YAMLファイルを読み込み、グループのリストを返す。
    各グループは辞書 { "name": group_name, "axes": [Axis, ...] } の形とする。
    YAML上の各軸の設定は、bss.config に存在する場合はその情報 (val2pulse, sense)
    を上書きして Axis オブジェクトに変換する。
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

    bss_axes = parse_bss_config(BSS_CONFIG_PATH)
    bss_axes_map = {axis.axis_name: axis for axis in bss_axes}

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
                            adisplay = adef.get("display", aname)
                            if aname in bss_axes_map:
                                aval2pulse = bss_axes_map[aname].val2pulse
                                asense = bss_axes_map[aname].sense
                            else:
                                aval2pulse = 1000
                                asense = 1
                            if aname:
                                axis_obj = Axis(aname, adisplay, aval2pulse, asense, unit="pulse")
                                axes_list.append(axis_obj)
                if grp_name:
                    groups.append({
                        "name": grp_name,
                        "axes": axes_list
                    })
    return groups


# --- GUI アプリケーション ---
class AxisToolApp:
    def __init__(self, root, config_groups):
        self.root = root
        self.root.title("Axis Tool GUI")
        self.config_groups = config_groups
        self.favorite_list = []  # お気に入り（最新優先、最大10軸）

        # 上部バー
        top_frame = tk.Frame(root)
        top_frame.pack(side=tk.TOP, fill=tk.X, padx=5, pady=5)

        # グループ選択
        self.group_var = tk.StringVar()
        group_names = [g["name"] for g in config_groups]
        if group_names:
            self.group_var.set(group_names[0])
        self.option_group = tk.OptionMenu(top_frame, self.group_var, *group_names, command=self.on_group_changed)
        self.option_group.pack(side=tk.LEFT)

        # 単位選択（pulse / mm）
        self.unit_var = tk.StringVar(value="pulse")
        tk.Radiobutton(top_frame, text="pulse", variable=self.unit_var, value="pulse", command=self.on_unit_changed).pack(side=tk.LEFT, padx=5)
        tk.Radiobutton(top_frame, text="mm", variable=self.unit_var, value="mm", command=self.on_unit_changed).pack(side=tk.LEFT, padx=5)

        # 軸表示モード選択（name or display）
        tk.Label(top_frame, text="Axis Label:").pack(side=tk.LEFT, padx=(10,2))
        self.axis_label_mode = tk.StringVar(value="display")
        axis_label_menu = tk.OptionMenu(top_frame, self.axis_label_mode, "name", "display", command=lambda _: self.on_axis_label_mode_changed())
        axis_label_menu.pack(side=tk.LEFT)

        btn_update = tk.Button(top_frame, text="Update", command=self.poll_all_axes)
        btn_update.pack(side=tk.LEFT, padx=5)

        # ★ 新規追加 ★ 保存／読み込み用ボタン
        self.btn_save_favorite = tk.Button(top_frame, text="Save Favorite", command=self.save_favorite)
        if self.group_var.get() == "favorite":
            self.btn_save_favorite.pack(side=tk.LEFT, padx=5)
        else:
            self.btn_save_favorite.pack_forget()

        # 中央（スクロール対応）
        self.main_frame = tk.Frame(root)
        self.main_frame.pack(side=tk.TOP, fill=tk.BOTH, expand=True)
        self.canvas = tk.Canvas(self.main_frame)
        self.canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.scrollbar = tk.Scrollbar(self.main_frame, orient=tk.VERTICAL, command=self.canvas.yview)
        self.scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.canvas.configure(yscrollcommand=self.scrollbar.set)
        self.inner_frame = tk.Frame(self.canvas)
        self.canvas.create_window((0, 0), window=self.inner_frame, anchor="nw")
        self.inner_frame.bind("<Configure>", self.on_inner_frame_configure)

        # 下部（コメント & Save current value）
        comment_frame = tk.Frame(root)
        comment_frame.pack(side=tk.TOP, fill=tk.X, padx=5, pady=5)
        tk.Label(comment_frame, text="Comment: ").pack(side=tk.LEFT)
        self.comment_text = tk.Entry(comment_frame, width=50)
        self.comment_text.pack(side=tk.LEFT, padx=5)

        btn_save = tk.Button(root, text="Save current value", command=self.on_save_button)
        btn_save.pack(side=tk.TOP, pady=5)

        # 内部管理：各軸ごとのウィジェットを保持（キーは axis.axis_name）
        self.axis_widgets = {}
        self.bg_default = {}
        if group_names:
            self.build_axes_for_group(group_names[0])

    # ---------- ヘルパー ----------
    def get_axis_label_text(self, axis: Axis):
        if self.axis_label_mode.get() == "name":
            return axis.axis_name
        else:
            return axis.display

    def on_axis_label_mode_changed(self, *args):
        self.build_axes_for_group(self.group_var.get())

    # ---------- スクロール更新 ----------
    def on_inner_frame_configure(self, event):
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))

    # ---------- イベント ----------
    def on_group_changed(self, new_group):
        self.build_axes_for_group(new_group)
        self.poll_all_axes()
        if new_group == "favorite":
            self.btn_save_favorite.pack(side=tk.LEFT, padx=5)
        else:
            self.btn_save_favorite.pack_forget()

    def on_unit_changed(self):
        self.update_all_positions()
        self.poll_all_axes()

    def on_save_button(self):
        self.update_all_positions()
        self.save_current_value()

    # ---------- グループ表示 ----------
    def build_axes_for_group(self, group_name):
        for child in self.inner_frame.winfo_children():
            child.destroy()
        self.axis_widgets.clear()
        self.bg_default.clear()
        group_info = None
        for g in self.config_groups:
            if g["name"] == group_name:
                group_info = g
                break
        if not group_info:
            return
        axes_list = self.favorite_list if group_name == "favorite" else group_info.get("axes", [])
        for axis in axes_list:
            axis_name = axis.axis_name
            label_text = self.get_axis_label_text(axis)
            row_frame = tk.Frame(self.inner_frame)
            row_frame.pack(side=tk.TOP, fill=tk.X, padx=5, pady=3)
            # 軸名ラベル（ダブルクリックでお気に入り切替）
            lbl_axis = tk.Label(row_frame, text=label_text, width=20, anchor="w")
            lbl_axis.pack(side=tk.LEFT)
            lbl_axis.bind("<Double-Button-1>", lambda e, ax=axis: self.toggle_favorite(ax))
            # 位置表示
            pos_var = tk.StringVar(value="---")
            lbl_pos = tk.Label(row_frame, textvariable=pos_var, width=15, anchor="e")
            lbl_pos.pack(side=tk.LEFT, padx=(5, 10))
            self.bg_default[axis_name] = lbl_pos.cget("bg")
            # 入力エリア
            entry_var = tk.StringVar()
            ent = tk.Entry(row_frame, textvariable=entry_var, width=8)
            ent.pack(side=tk.LEFT, padx=(0, 5))
            # 各ボタン
            btn_abs = tk.Button(row_frame, text="abs", command=lambda ax=axis: self.abs_axis(ax))
            btn_abs.pack(side=tk.LEFT, padx=2)
            btn_plus = tk.Button(row_frame, text="+", command=lambda ax=axis: self.plus_axis(ax))
            btn_plus.pack(side=tk.LEFT, padx=2)
            btn_minus = tk.Button(row_frame, text="-", command=lambda ax=axis: self.minus_axis(ax))
            btn_minus.pack(side=tk.LEFT, padx=2)
            btn_stop = tk.Button(row_frame, text="stop", command=lambda ax=axis: self.stop_axis(ax))
            btn_stop.pack(side=tk.LEFT, padx=2)
            if self.is_in_favorite(axis.axis_name):
                lbl_axis.config(bg="lightblue")
            self.axis_widgets[axis_name] = {
                "pos_var": pos_var,
                "pos_label": lbl_pos,
                "entry_var": entry_var,
                "val2pulse": axis.val2pulse,
                "sense": axis.sense,
                "axis_label": lbl_axis
            }
        self.inner_frame.update_idletasks()
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))

    # ---------- Favorite管理 ----------
    def is_in_favorite(self, axis_name: str) -> bool:
        return any(a.axis_name == axis_name for a in self.favorite_list)

    def toggle_favorite(self, axis: Axis):
        axis_name = axis.axis_name
        if self.is_in_favorite(axis_name):
            self.favorite_list = [a for a in self.favorite_list if a.axis_name != axis_name]
        else:
            self.favorite_list = [a for a in self.favorite_list if a.axis_name != axis_name]
            self.favorite_list.insert(0, axis.copy())
            if len(self.favorite_list) > 10:
                self.favorite_list = self.favorite_list[:10]
        self.sync_favorite_group()
        if axis_name in self.axis_widgets:
            if self.is_in_favorite(axis_name):
                self.axis_widgets[axis_name]["axis_label"].config(bg="lightblue")
            else:
                self.axis_widgets[axis_name]["axis_label"].config(bg=self.bg_default[axis_name])
        if self.group_var.get() == "favorite":
            self.build_axes_for_group("favorite")

    def add_to_favorite_on_move(self, axis: Axis):
        axis_name = axis.axis_name
        self.favorite_list = [a for a in self.favorite_list if a.axis_name != axis_name]
        self.favorite_list.insert(0, axis.copy())
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
    def abs_axis(self, axis: Axis):
        axis_name = axis.axis_name
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
        s = axis.sense
        if self.unit_var.get() == "mm":
            pos = int(round((fv * axis.val2pulse) / s))
        else:
            pos = int(round(fv / s))
        if put_position(axis, pos):
            self.poll_axis(axis)
            self.add_to_favorite_on_move(axis)
        else:
            print(f"[Error] abs command failed for {axis_name}")

    def plus_axis(self, axis: Axis):
        axis_name = axis.axis_name
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
        s = axis.sense
        st, cur_pulse = fetch_state_and_position(axis)
        if self.unit_var.get() == "mm":
            diff = int(round((fv * axis.val2pulse) / s))
        else:
            diff = int(round(fv / s))
        new_pos = cur_pulse + diff
        if put_position(axis, new_pos):
            self.poll_axis(axis)
            self.add_to_favorite_on_move(axis)
        else:
            print(f"[Error] plus command failed for {axis_name}")

    def minus_axis(self, axis: Axis):
        axis_name = axis.axis_name
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
        s = axis.sense
        st, cur_pulse = fetch_state_and_position(axis)
        if self.unit_var.get() == "mm":
            diff = int(round((fv * axis.val2pulse) / s))
        else:
            diff = int(round(fv / s))
        new_pos = cur_pulse - diff
        if put_position(axis, new_pos):
            self.poll_axis(axis)
            self.add_to_favorite_on_move(axis)
        else:
            print(f"[Error] minus command failed for {axis_name}")

    def stop_axis(self, axis: Axis):
        if put_stop(axis):
            print(f"[Info] Stopped axis: {axis.axis_name}")
        else:
            print(f"[Error] stop command failed for {axis.axis_name}")

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
        axes_list = self.favorite_list if grp == "favorite" else group_info.get("axes", [])
        for ax in axes_list:
            self.poll_axis(ax)

    def poll_axis(self, axis: Axis):
        axis_name = axis.axis_name
        if axis_name not in self.axis_widgets:
            return
        st, pos_int = fetch_state_and_position(axis)
        w = self.axis_widgets[axis_name]
        s = axis.sense
        if axis.unit == "mm":
            w["pos_var"].set(f"{pos_int * s} mm")
        elif self.unit_var.get() == "pulse":
            w["pos_var"].set(f"{pos_int * s} pulse")
        else:
            mm_val = (pos_int * s) / axis.val2pulse
            w["pos_var"].set(f"{mm_val:.3f} mm")
        lbl_pos = w["pos_label"]
        if st.lower() != "inactive":
            lbl_pos.config(bg="yellow")
            self.root.after(1000, lambda: self.poll_axis(axis))
        else:
            lbl_pos.config(bg=self.bg_default[axis_name])

    def update_all_positions(self):
        for axis_name, wdict in self.axis_widgets.items():
            txt = wdict["pos_var"].get()
            if not txt or txt in ("---", ""):
                continue
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

    def save_current_value(self):
        grp = self.group_var.get()
        cmt = self.comment_text.get().strip()
        now_str = datetime.datetime.now().strftime("%Y/%m/%d %H:%M:%S")
        date_str = datetime.datetime.now().strftime("%Y%m%d")
        filename = f"{date_str}_{grp}.yaml"
        group_info = None
        for g in self.config_groups:
            if g["name"] == grp:
                group_info = g
                break
        if not group_info:
            return
        axes_list = self.favorite_list if grp == "favorite" else group_info.get("axes", [])
        lines_axis = []
        for ax in axes_list:
            st, pos_pulse = fetch_state_and_position(ax)
            sense_val = ax.sense
            displayed = pos_pulse * sense_val
            time.sleep(0.1)
            lines_axis.append(f"      - {ax.axis_name}: {displayed} pulse")
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

    def save_favorite(self):
        """
        現在の favorite リストから各軸の情報を user_axis.yaml に保存する。
        YAML形式は、リスト内に { "group": {"name": <グループ名>, "axes": [ { "axis": {...} }, ...]}} の形とする。
        同名のグループがある場合は確認ダイアログで上書きする。
        """
        group_name = simpledialog.askstring("Group Name", "Enter group name for Favorite:", parent=self.root)
        if not group_name:
            return
        favorite_entry = {
            "group": {
                "name": group_name,
                "axes": [
                    {"axis": {"name": ax.axis_name, "display": ax.display}}
                    for ax in self.favorite_list
                ]
            }
        }
        filename = "user_axis.yaml"
        if os.path.exists(filename):
            try:
                with open(filename, "r", encoding="utf-8") as f:
                    existing_data = yaml.safe_load(f)
                    if existing_data is None:
                        existing_data = []
            except Exception as e:
                messagebox.showerror("Error", f"Failed to load '{filename}': {e}")
                return
        else:
            existing_data = []
        for i, entry in enumerate(existing_data):
            if "group" in entry and entry["group"].get("name") == group_name:
                if not messagebox.askokcancel("Confirm Overwrite", f"Group '{group_name}' already exists. Overwrite?"):
                    return
                existing_data.pop(i)
                break
        existing_data.append(favorite_entry)
        try:
            with open(filename, "w", encoding="utf-8") as f:
                yaml.dump(existing_data, f, allow_unicode=True)
            messagebox.showinfo("Success", f"Favorite group '{group_name}' saved successfully in '{filename}'.")
        except Exception as e:
            messagebox.showerror("Error", f"Failed to save '{filename}': {e}")

    def load_user_group(self):
        """
        user_group.yaml を読み込み、保存されている各グループを反映する。
        各グループは { "group": {"name": <グループ名>, "axes": [ { "axis": {"name": <軸名>, "display": <表示名>} } ]}} の形式とする。
        読み込んだグループ名は OptionMenu に追加される。
        """
        from tkinter import messagebox
        try:
            with open("user_group.yaml", "r", encoding="utf-8") as f:
                data = yaml.safe_load(f)
            if data is None:
                messagebox.showwarning("Warning", "user_group.yaml is empty.")
                return
        except Exception as e:
            messagebox.showerror("Error", f"Failed to load 'user_group.yaml': {e}")
            return

        loaded_groups = []
        for item in data:
            if "group" in item:
                grp = item["group"]
                loaded_groups.append(grp)

        if not loaded_groups:
            messagebox.showwarning("Warning", "No valid group found in 'user_group.yaml'.")
            return

        for grp in loaded_groups:
            group_name = grp.get("name")
            if not group_name:
                continue
            found = False
            for idx, existing_grp in enumerate(self.config_groups):
                if existing_grp.get("name") == group_name:
                    self.config_groups[idx]["axes"] = grp.get("axes", [])
                    found = True
                    break
            if not found:
                self.config_groups.append(grp)

        menu = self.option_group["menu"]
        menu.delete(0, "end")
        for grp in self.config_groups:
            grp_name = grp.get("name")
            menu.add_command(label=grp_name, command=lambda value=grp_name: self.group_var.set(value))

        messagebox.showinfo("Success", "User groups loaded from 'user_group.yaml'.")
        self.build_axes_for_group(self.group_var.get())


# --- グループ情報マージ ---
def load_all_configs():
    """
    起動時に、default_axis.yaml と user_axis.yaml の両方からグループ情報を読み込み、
    同名のグループがあれば軸リストを結合し、グループ名でソートしたリストを返す。
    戻り値:
      [ { "name": <グループ名>, "axes": [Axis, ...] }, ... ]
    """
    groups_default = load_config("default_axis.yaml")
    if os.path.exists("user_axis.yaml"):
        groups_user = load_config("user_axis.yaml")
    else:
        groups_user = []

    merged = {}
    for grp in groups_default:
        name = grp.get("name")
        if name:
            merged[name] = grp.copy()

    for grp in groups_user:
        name = grp.get("name")
        if not name:
            continue
        if name in merged:
            existing_axes = merged[name].get("axes", [])
            user_axes = grp.get("axes", [])
            # 既存軸名のセット（重複防止）
            existing_axis_names = {ax.axis_name for ax in existing_axes}
            for uax in user_axes:
                if uax.axis_name not in existing_axis_names:
                    existing_axes.append(uax)
            merged[name]["axes"] = existing_axes
        else:
            merged[name] = grp.copy()

    sorted_groups = sorted(merged.values(), key=lambda x: x.get("name", ""))
    return sorted_groups


# --- メイン関数 ---
def main():
    """
    起動時に default_axis.yaml と user_axis.yaml の両方からグループ情報を読み込み、
    bss.config から全軸情報（"all" グループ）および空の "favorite" グループを追加して GUI に渡す。
    """
    config_groups = load_all_configs()
    all_axes = parse_bss_config(BSS_CONFIG_PATH)
    config_groups.append({"name": "all", "axes": all_axes})
    config_groups.append({"name": "favorite", "axes": []})
    root = tk.Tk()
    app = AxisToolApp(root, config_groups)
    root.mainloop()


if __name__ == "__main__":
    main()

