#!/usr/bin/env python
import sys
import socket
import tkinter as tk
import yaml
import datetime
import os
import time
import asyncio
import threading
from asyncio import Lock
from tkinter import simpledialog, messagebox

# BSS_CONFIG_PATH: envの${BLCONFIG}を使用。設定されていない場合はデフォルト値
BSS_CONFIG_PATH = os.path.join(os.environ.get("BLCONFIG", "/blconfig"), "bss/bss.config")

# デフォルト値（bss.configが読めない場合の fallback）
HOST = "10.178.215.3"
PORT = 10101
BL_OBJ = "bl_41in"

# bss.config からHOST, PORT, BL_OBJを読み込み
def load_bss_network_config(config_path: str):
    global HOST, PORT, BL_OBJ
    if not os.path.exists(config_path):
        print(f"[Warn] bss.config not found: {config_path}, using default network settings")
        return

    try:
        with open(config_path, "r", encoding="utf-8") as f:
            lines = f.readlines()

        for line in lines:
            s = line.strip()
            if s.startswith("Ms_IP:"):
                HOST = s.split(":", 1)[1].strip()
            elif s.startswith("Ms_Port:"):
                try:
                    PORT = int(s.split(":", 1)[1].strip())
                except ValueError:
                    pass
            elif s.startswith("BL_Object:"):
                bl_obj = s.split(":", 1)[1].strip()
                BL_OBJ = f"bl_{bl_obj}"

        print(f"[Info] Loaded network config: HOST={HOST}, PORT={PORT}, BL_OBJ={BL_OBJ}")
    except Exception as e:
        print(f"[Error] Failed to parse network settings from bss.config: {e}")

# 起動時に設定を読み込み
load_bss_network_config(BSS_CONFIG_PATH)


# --- Axisクラス ---
class Axis:
    def __init__(self, axis_name: str, display: str = None, val2pulse: int = 1000, sense: int = 1, unit: str = "pulse"):
        self.axis_name = axis_name
        self.display = display if display is not None else axis_name
        self.val2pulse = val2pulse
        self.sense = sense
        self.unit = unit
        # リミット状態のフラグ（初期値はすべてFalse）
        self.cw_hard_limit = False   # 正方向ハードリミット
        self.ccw_hard_limit = False  # 負方向ハードリミット
        self.cw_soft_limit = False   # 正方向ソフトリミット
        self.ccw_soft_limit = False  # 負方向ソフトリミット
        self.home_position = False   # ホームポジション
        self.status_decimal = 0      # ステータス（10進数形式）

    def update_status_flags(self, decimal_value: int):
        """
        5ビットのステータス値（10進数）から各フラグを更新する

        Args:
            decimal_value: 5ビットのステータス値（10進数）
        """
        if not isinstance(decimal_value, int):
            return

        # 10進数の値を保存
        self.status_decimal = decimal_value

        # 各ビットに対応するフラグを更新
        self.cw_hard_limit = bool(decimal_value & 0b00001)  # bit 0
        self.ccw_hard_limit = bool(decimal_value & 0b00010) # bit 1
        self.cw_soft_limit = bool(decimal_value & 0b00100)  # bit 2
        self.ccw_soft_limit = bool(decimal_value & 0b01000) # bit 3
        self.home_position = bool(decimal_value & 0b10000)  # bit 4

    def copy(self):
        axis_copy = Axis(self.axis_name, self.display, self.val2pulse, self.sense, self.unit)
        # 状態フラグもコピー
        axis_copy.cw_hard_limit = self.cw_hard_limit
        axis_copy.ccw_hard_limit = self.ccw_hard_limit
        axis_copy.cw_soft_limit = self.cw_soft_limit
        axis_copy.ccw_soft_limit = self.ccw_soft_limit
        axis_copy.home_position = self.home_position
        axis_copy.status_decimal = self.status_decimal
        return axis_copy

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
        cunit = "pulse"  # デフォルトは pulse
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
            elif l.startswith("_cunit:"):
                # _cunitの読み取り
                cunit_tmp = l.split(":", 1)[1].strip()
                if cunit_tmp in ["mm", "deg", "mrad", "angstroam", "kev", "pulse"]:
                    cunit = cunit_tmp
        if axis_name:
            display = axis_comment.split(",", 1)[0].strip() if axis_comment else axis_name
            # cunitを持つAxisオブジェクトを作成
            result.append(Axis(axis_name, display, val2pulse, sense, unit=cunit))
    return result


# --- 通信用関数 ---
def fetch_state_and_position(axis: Axis):
    """
    "get/{BL_OBJ}_{axis.axis_name}/query" などを送信し、
    応答から (state, position, error_flag) を返す

    Returns:
        tuple: (state, position, error_flag)
        - state: 軸の状態 ("inactive", "moving", etc.)
        - position: 位置情報 (pulse または mm)
        - error_flag: 通信エラーなどのエラーが発生した場合 True
    """
    axis_name = axis.axis_name
    st = "inactive"  # デフォルト状態

    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.settimeout(2.0)  # タイムアウト設定
                s.connect((HOST, PORT))

                # まず query コマンドを送信
                cmd = f"get/{BL_OBJ}_{axis_name}/query\n"
                print("[Send]", cmd.strip())
                s.sendall(cmd.encode("utf-8"))
                resp = s.recv(1024).decode("utf-8").strip()
                print("[Recv]", resp)

                # 空応答やエラー応答の検出
                if not resp or "error" in resp.lower():
                    print(f"[Error] Invalid response for {axis_name}: {resp}")
                    return ("error", 0, True)

                # クエリ応答の解析
                splitted = resp.split('/')
                query_has_position = False  # queryに位置情報が含まれているかのフラグ

                if len(splitted) > 3:
                    part = splitted[3]

                    # 1. query応答で直接単位つきで値が返ってくる場合の処理
                    units = ["mm", "deg", "mrad", "angstroam", "kev"]  # 対応する単位リスト
                    found_unit = None

                    # どの単位が含まれているか確認
                    for unit in units:
                        if unit in part and ('_' in part):
                            found_unit = unit
                            break

                    if found_unit:
                        # 例: inactive_7.150mm または ok_1.000deg のようなフォーマット
                        try:
                            state_part, pos_part = part.split('_', 1)
                            # "ok_"からはじまる形式は"inactive_"と同様に処理
                            if state_part == "ok":
                                st = "inactive"
                            else:
                                st = state_part

                            pos = float(pos_part.replace(found_unit, "").strip())
                            # 応答の単位を覚えておく
                            axis.unit = found_unit
                            query_has_position = True
                            return (st, pos, False)
                        except (ValueError, IndexError):
                            pass

                    # 2. 状態情報の抽出（位置情報付きの場合）
                    if part == "ok":
                        st = "inactive"
                    elif part == "0" and len(splitted) > 2 and splitted[2] in ["ok", "active"]:
                        # /ok/0 や /active/0 のパターン
                        st = "inactive" if splitted[2] == "ok" else splitted[2]
                    elif '_' in part:
                        # 例: moving_12345pulse
                        try:
                            st, pos_str = part.split('_', 1)
                            try:
                                pos_int = int(pos_str.replace("pulse", "").strip())
                                query_has_position = True
                                return (st, pos_int, False)
                            except ValueError:
                                pass
                        except (ValueError, IndexError):
                            if part.startswith("inactive_") or part.startswith("moving_"):
                                st = part.split('_')[0]

                # queryだけでは位置がわからない場合、別のコマンドで位置を取得
                if not query_has_position:
                    # 位置取得コマンドを決定
                    position_cmd = None

                    # 軸の単位に基づいてコマンドを選択
                    if axis.unit in ["deg", "mrad"] or axis_name.endswith("angle"):
                        position_cmd = f"get/{BL_OBJ}_{axis_name}/angle\n"
                    elif axis_name.endswith("width") or axis_name.endswith("height"):
                        position_cmd = f"get/{BL_OBJ}_{axis_name}/aperture\n"
                    else:
                        # デフォルトはpositionコマンド
                        position_cmd = f"get/{BL_OBJ}_{axis_name}/position\n"

                    print("[Send]", position_cmd.strip())
                    s.sendall(position_cmd.encode("utf-8"))
                    resp = s.recv(1024).decode("utf-8").strip()
                    print("[Recv]", resp)

                    # 空応答やエラー応答の検出
                    if not resp or "error" in resp.lower():
                        print(f"[Error] Invalid position response for {axis_name}: {resp}")
                        return ("error", 0, True)

                    splitted = resp.split('/')
                    if len(splitted) > 3:
                        part = splitted[3]
                        # 位置情報から単位を検出
                        pos_value = None
                        detected_unit = None

                        for unit in ["mm", "deg", "mrad", "angstroam", "kev", "pulse"]:
                            if unit in part:
                                try:
                                    pos_value = float(part.replace(unit, "").strip())
                                    detected_unit = unit
                                    break
                                except ValueError:
                                    pass

                        # 数値のみの場合（単位なし）
                        if pos_value is None:
                            try:
                                pos_value = float(part)
                                detected_unit = axis.unit  # 既存の単位を使用
                            except ValueError:
                                pass

                        if pos_value is not None:
                            if detected_unit and detected_unit != "pulse":
                                axis.unit = detected_unit
                            return (st, pos_value, False)

                # 位置情報が全く取得できない場合
                return (st, 0, False)

            except socket.timeout:
                print(f"[Error] Communication timeout for {axis_name}")
                return ("error", 0, True)
            except ConnectionRefusedError:
                print(f"[Error] Connection refused for {axis_name}")
                return ("error", 0, True)
            except Exception as e:
                print(f"[Error] Socket error for {axis_name}: {e}")
                return ("error", 0, True)

        # デフォルト応答（通常はここには到達しない）
        return ("inactive", 0, False)

    except Exception as e:
        print(f"[Error] Unexpected error for {axis_name}: {e}")
        return ("error", 0, True)

def put_position(axis: Axis, position: float) -> bool:
    """
    単位に応じたputコマンドを送信し、応答が "/0" で終わるなら True を返す
    - "put/{BL_OBJ}_{axis.axis_name}/{position}pulse"
    - "put/{BL_OBJ}_{axis.axis_name}/{position}mm"
    - "put/{BL_OBJ}_{axis.axis_name}/{position}deg"
    - "put/{BL_OBJ}_{axis.axis_name}/{position}mrad"
    """
    axis_name = axis.axis_name
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.settimeout(2.0)
                s.connect((HOST, PORT))

                # 軸の単位に応じて送信コマンドを作成
                unit_suffix = "pulse"  # デフォルト単位

                # 特殊単位の場合は、その単位をそのまま使用
                # mm, deg, mrad のいずれかの単位が設定されていれば、その単位を使用
                if axis.unit in ["mm", "deg", "mrad", "angstroam", "kev"]:
                    unit_suffix = axis.unit
                    # 単位付きの軸はそのままの値を送信
                    value_to_send = position
                else:
                    # pulse単位の軸は整数に変換
                    value_to_send = int(position)

                cmd = f"put/{BL_OBJ}_{axis_name}/{value_to_send}{unit_suffix}\n"
                print("[Send]", cmd.strip())
                s.sendall(cmd.encode("utf-8"))
                resp = s.recv(1024).decode("utf-8").strip()
                print("[Recv]", resp)

                if not resp:
                    print(f"[Error] Empty response for put_position of {axis_name}")
                    return False

                # 応答の最後が"/0"であればTrue
                return resp.endswith("/0")
            except socket.timeout:
                print(f"[Error] Timeout in put_position for {axis_name}")
                return False
            except ConnectionRefusedError:
                print(f"[Error] Connection refused in put_position for {axis_name}")
                return False
            except Exception as e:
                print(f"[Error] Socket error in put_position for {axis_name}: {e}")
                return False
    except Exception as e:
        print(f"[Error] Unexpected error in put_position for {axis_name}: {e}")
        return False


def put_stop(axis: Axis) -> bool:
    """
    "put/{BL_OBJ}_{axis.axis_name}/stop" を送信し、
    応答が "/0" で終わるなら True を返す
    """
    axis_name = axis.axis_name
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.settimeout(2.0)
                s.connect((HOST, PORT))
                cmd = f"put/{BL_OBJ}_{axis_name}/stop\n"
                print("[Send]", cmd.strip())
                s.sendall(cmd.encode("utf-8"))
                resp = s.recv(1024).decode("utf-8").strip()
                print("[Recv]", resp)

                if not resp:
                    print(f"[Error] Empty response for put_stop of {axis_name}")
                    return False

                # 応答の最後が"/0"であればTrue
                return resp.endswith("/0")
            except socket.timeout:
                print(f"[Error] Timeout in put_stop for {axis_name}")
                return False
            except ConnectionRefusedError:
                print(f"[Error] Connection refused in put_stop for {axis_name}")
                return False
            except Exception as e:
                print(f"[Error] Socket error in put_stop for {axis_name}: {e}")
                return False
    except Exception as e:
        print(f"[Error] Unexpected error in put_stop for {axis_name}: {e}")
        return False


def fetch_axis_status(axis: Axis) -> bool:
    """
    "get/{BL_OBJ}_{axis.axis_name}/status" を送信し、
    軸のステータス情報（リミット状態など）を取得して Axis オブジェクトを更新する

    Returns:
        bool: 成功の場合 True、失敗の場合 False
    """
    axis_name = axis.axis_name
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.settimeout(2.0)
                s.connect((HOST, PORT))
                cmd = f"get/{BL_OBJ}_{axis_name}/status\n"
                print("[Send]", cmd.strip())
                s.sendall(cmd.encode("utf-8"))
                resp = s.recv(1024).decode("utf-8").strip()
                print("[Recv]", resp)

                if not resp:
                    print(f"[Error] Empty response for status of {axis_name}")
                    return False

                # 応答を解析
                splitted = resp.split('/')
                if len(splitted) > 3:
                    status_str = splitted[3]
                    try:
                        # 10進数文字列を整数に変換
                        status_value = int(status_str)
                        # 軸のステータスフラグを更新
                        axis.update_status_flags(status_value)
                        return True
                    except ValueError:
                        print(f"[Error] Invalid status value for {axis_name}: {status_str}")

                return False
            except socket.timeout:
                print(f"[Error] Timeout in fetch_axis_status for {axis_name}")
                return False
            except ConnectionRefusedError:
                print(f"[Error] Connection refused in fetch_axis_status for {axis_name}")
                return False
            except Exception as e:
                print(f"[Error] Socket error in fetch_axis_status for {axis_name}: {e}")
                return False
    except Exception as e:
        print(f"[Error] Unexpected error in fetch_axis_status for {axis_name}: {e}")
        return False


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
                                aunit = bss_axes_map[aname].unit  # cunitもコピー
                            else:
                                aval2pulse = 1000
                                asense = 1
                                aunit = "pulse"
                            if aname:
                                axis_obj = Axis(aname, adisplay, aval2pulse, asense, unit=aunit)
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
        self.error_axes = set()  # エラーが発生した軸のセット（再ポーリングしない）
        self.status_disabled_axes = set()  # statusコマンドが失敗した軸のセット

        # 非同期ポーリングの管理
        self.polling_tasks = {}  # 軸ごとのポーリングタスクを管理
        self.loop = None  # asyncioのイベントループ
        self.polling_thread = None  # ポーリング用スレッド
        self.is_shutting_down = False  # シャットダウンフラグ
        self.socket_lock = None  # ソケット通信用の排他ロック（初期化はsetup_async_pollingで行う）

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

        # 単位選択（pulse / mm）- 通常の軸のみに適用
        unit_frame = tk.LabelFrame(top_frame, text="Display Unit (for pulse axes)")
        unit_frame.pack(side=tk.LEFT, padx=10, pady=2)

        self.unit_var = tk.StringVar(value="pulse")
        tk.Radiobutton(unit_frame, text="pulse", variable=self.unit_var, value="pulse", command=self.on_unit_changed).pack(side=tk.LEFT, padx=5)
        tk.Radiobutton(unit_frame, text="mm", variable=self.unit_var, value="mm", command=self.on_unit_changed).pack(side=tk.LEFT, padx=5)

        # 軸表示モード選択（name or display）
        tk.Label(top_frame, text="Axis Label:").pack(side=tk.LEFT, padx=(10,2))
        self.axis_label_mode = tk.StringVar(value="display")
        axis_label_menu = tk.OptionMenu(top_frame, self.axis_label_mode, "name", "display", command=lambda _: self.on_axis_label_mode_changed())
        axis_label_menu.pack(side=tk.LEFT)

        btn_update = tk.Button(top_frame, text="Update", command=self.poll_all_axes)
        btn_update.pack(side=tk.LEFT, padx=5)

        # リセットボタン（エラー軸のリセット）
        btn_reset_errors = tk.Button(top_frame, text="Reset Errors", command=self.reset_error_axes)
        btn_reset_errors.pack(side=tk.LEFT, padx=5)

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

        # 非同期ポーリングのセットアップ（axis_unit_cacheの初期化も含む）
        self.setup_async_polling()

        # 軸の構築（setup_async_pollingの後で実行）
        if group_names:
            self.build_axes_for_group(group_names[0])

        # アプリケーションの終了時にポーリングを停止するためのプロトコル
        self.root.protocol("WM_DELETE_WINDOW", self.on_closing)

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
        # グループ変更時にエラー軸リストとstatus無効リストをクリア
        self.error_axes.clear()
        self.status_disabled_axes.clear()

        # 実行中のタスクをすべてキャンセル
        if self.loop:
            for task in self.polling_tasks.values():
                if not task.done():
                    self.loop.call_soon_threadsafe(task.cancel)
            self.polling_tasks.clear()

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

    def reset_error_axes(self):
        """エラー軸リストとstatus無効リストをクリアしてすべての軸のポーリングを再開する"""
        num_errors = len(self.error_axes)
        num_status_disabled = len(self.status_disabled_axes)

        if not self.error_axes and not self.status_disabled_axes:
            return

        print(f"[Info] Resetting {num_errors} axes with errors and {num_status_disabled} axes with disabled status")
        self.error_axes.clear()
        self.status_disabled_axes.clear()

        # 実行中のタスクをすべてキャンセル
        if self.loop:
            for task in self.polling_tasks.values():
                if not task.done():
                    self.loop.call_soon_threadsafe(task.cancel)
            self.polling_tasks.clear()

        # 全軸のポーリングを再開
        self.poll_all_axes()

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
            # キャッシュから単位情報を読み込み
            if axis_name in self.axis_unit_cache:
                axis.unit = self.axis_unit_cache[axis_name]
            label_text = self.get_axis_label_text(axis)
            row_frame = tk.Frame(self.inner_frame)
            row_frame.pack(side=tk.TOP, fill=tk.X, padx=5, pady=3)
            # 軸名ラベル（ダブルクリックでお気に入り切替）
            lbl_axis = tk.Label(row_frame, text=label_text, width=20, anchor="w")
            lbl_axis.pack(side=tk.LEFT)
            lbl_axis.bind("<Double-Button-1>", lambda e, ax=axis: self.toggle_favorite(ax))

            # リミット状態表示（5つのボックス）
            limit_frame = tk.Frame(row_frame)
            limit_frame.pack(side=tk.LEFT, padx=(2, 5))

            # 5つのリミットステータス
            limit_labels = []
            for i in range(5):
                lbl = tk.Label(limit_frame, text="□", width=1, font=("Courier", 10))
                lbl.pack(side=tk.LEFT, padx=0)
                limit_labels.append(lbl)

            # 位置表示
            pos_var = tk.StringVar(value="---")
            lbl_pos = tk.Label(row_frame, textvariable=pos_var, width=15, anchor="e")
            # 軸のユニットに応じて文字色を設定
            if axis.unit in ["mm", "deg", "mrad", "angstroam", "kev"]:
                lbl_pos.config(fg="blue")  # 特殊単位の軸は青色
            else:
                lbl_pos.config(fg="black")  # 通常のpulse軸は黒色
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
                "axis_label": lbl_axis,
                "limit_labels": limit_labels,
                "unit": axis.unit  # 単位情報も保存
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

        # GUIモードと軸タイプに応じた入力値の処理
        if self.unit_var.get() == "pulse":
            # GUIがpulseモードの場合
            if axis.unit in ["mm", "deg", "mrad", "angstroam", "kev"]:
                # 特殊単位の軸へのパルス値入力
                # パルス値を特殊単位に変換して送信
                unit_val = fv / axis.val2pulse
                pos = unit_val * s  # センス値は適用
                expected_pos_pulse = int(fv)  # 予想位置はパルス値
            else:
                # 通常のpulse軸の場合
                pos = int(round(fv / s))
                expected_pos_pulse = pos
        else:
            # GUIがmmモードの場合
            if axis.unit in ["mm", "deg", "mrad", "angstroam", "kev"]:
                # 特殊単位の軸への値入力 (mmで入力されている場合も元の単位で送信)
                pos = fv * s  # センス値は適用
                expected_pos_pulse = int(pos * axis.val2pulse)
            else:
                # 通常のpulse軸を操作する場合
                pos = int(round((fv * axis.val2pulse) / s))
                expected_pos_pulse = pos

        if put_position(axis, pos):
            # 移動先の予想位置と共に非同期ポーリングを開始
            self.start_polling_task(axis, expected_pos=expected_pos_pulse, after_move=True)
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
        st, cur_pos, error_flag = fetch_state_and_position(axis)
        if error_flag:
            print(f"[Error] Could not get current position for {axis_name}")
            return

        # GUIモードと軸タイプに応じた入力値の処理
        if self.unit_var.get() == "pulse":
            # GUIがpulseモードの場合
            if axis.unit in ["mm", "deg", "mrad", "angstroam", "kev"]:
                # 特殊単位の軸へのパルス値入力
                diff = fv / axis.val2pulse  # パルス値を特殊単位に変換
                new_pos = cur_pos + (diff * s)  # センス値を考慮して加算
                expected_pos_pulse = int(cur_pos * axis.val2pulse + fv)  # 予想位置はパルス値
            else:
                # 通常のpulse軸の場合
                diff = int(round(fv / s))
                new_pos = cur_pos + diff
                expected_pos_pulse = new_pos
        else:
            # GUIがmmモードの場合
            if axis.unit in ["mm", "deg", "mrad", "angstroam", "kev"]:
                # 特殊単位の軸への値入力
                diff = fv
                new_pos = cur_pos + (diff * s)  # センス値を考慮して加算
                expected_pos_pulse = int(new_pos * axis.val2pulse)
            else:
                # 通常のpulse軸の場合
                diff = int(round((fv * axis.val2pulse) / s))
                new_pos = cur_pos + diff
                expected_pos_pulse = new_pos

        if put_position(axis, new_pos):
            # 移動先の予想位置と共に非同期ポーリングを開始
            self.start_polling_task(axis, expected_pos=expected_pos_pulse, after_move=True)
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
        st, cur_pos, error_flag = fetch_state_and_position(axis)
        if error_flag:
            print(f"[Error] Could not get current position for {axis_name}")
            return

        # GUIモードと軸タイプに応じた入力値の処理
        if self.unit_var.get() == "pulse":
            # GUIがpulseモードの場合
            if axis.unit in ["mm", "deg", "mrad", "angstroam", "kev"]:
                # 特殊単位の軸へのパルス値入力
                diff = fv / axis.val2pulse  # パルス値を特殊単位に変換
                new_pos = cur_pos - (diff * s)  # センス値を考慮して減算
                expected_pos_pulse = int(cur_pos * axis.val2pulse - fv)  # 予想位置はパルス値
            else:
                # 通常のpulse軸の場合
                diff = int(round(fv / s))
                new_pos = cur_pos - diff
                expected_pos_pulse = new_pos
        else:
            # GUIがmmモードの場合
            if axis.unit in ["mm", "deg", "mrad", "angstroam", "kev"]:
                # 特殊単位の軸への値入力
                diff = fv
                new_pos = cur_pos - (diff * s)  # センス値を考慮して減算
                expected_pos_pulse = int(new_pos * axis.val2pulse)
            else:
                # 通常のpulse軸の場合
                diff = int(round((fv * axis.val2pulse) / s))
                new_pos = cur_pos - diff
                expected_pos_pulse = new_pos

        if put_position(axis, new_pos):
            # 移動先の予想位置と共に非同期ポーリングを開始
            self.start_polling_task(axis, expected_pos=expected_pos_pulse, after_move=True)
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
        """全ての軸のポーリングを開始する"""
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
            self.start_polling_task(ax)

    def poll_axis(self, axis: Axis, expected_pos=None, after_move=False, retry_count=0):
        """
        軸の状態と位置を取得して表示を更新する（非同期版のラッパー）

        Parameters:
        - axis: 対象の軸
        - expected_pos: 移動先の予想位置（pulseで指定）。移動命令直後の確認用
        - after_move: 移動命令直後かどうか
        - retry_count: リトライ回数（再帰呼び出し用）
        """
        # 非同期バージョンのポーリング処理を開始
        self.start_polling_task(axis, expected_pos, after_move, retry_count)

    def update_all_positions(self):
        """
        現在の表示単位に合わせて位置表示を更新する
        全ての軸タイプ（pulse, mm, deg, mrad）に対応
        """
        for axis_name, wdict in self.axis_widgets.items():
            # 該当する軸オブジェクトを見つける
            axis_obj = None
            for g in self.config_groups:
                for ax in g.get("axes", []):
                    if ax.axis_name == axis_name:
                        axis_obj = ax
                        break
                if axis_obj:
                    break

            if not axis_obj:
                continue

            # 表示テキストを取得
            txt = wdict["pos_var"].get()
            if not txt or txt in ("---", "", "ERROR"):
                continue

            # テキストから現在の値を抽出
            val2pulse = axis_obj.val2pulse
            sense = axis_obj.sense
            current_value = None
            unit_value = None

            # 特殊単位の軸（mm, deg, mrad）の場合
            if axis_obj.unit in ["mm", "deg", "mrad", "angstroam", "kev"]:
                # 特殊単位の軸でパルス表示モードの場合（例: "12345 pulse (1.234 mm)"）
                if "pulse" in txt and "(" in txt and ")" in txt:
                    # パルス値と単位値の両方を取得
                    pulse_part = txt.split("(")[0].strip()
                    unit_part = txt.split("(")[1].split(")")[0].strip()

                    try:
                        # パルス値を取得
                        pulse_value = int(pulse_part.replace("pulse", "").strip())
                        # 単位値を取得（例: "1.234 mm"）
                        unit_parts = unit_part.split()
                        if len(unit_parts) >= 2:
                            unit_value = float(unit_parts[0])
                            current_value = pulse_value  # パルス値を現在値として保存
                    except ValueError:
                        continue
                else:
                    # 単位値のみの表示（例: "1.234 mm"）
                    for unit in ["mm", "deg", "mrad", "angstroam", "kev"]:
                        if unit in txt:
                            try:
                                unit_value = float(txt.replace(unit, "").strip())
                                # 単位値からパルス値に変換
                                current_value = int(unit_value * val2pulse / sense)
                                break
                            except ValueError:
                                continue
            else:
                # 通常の軸（pulse単位）の場合
                for unit in ["pulse", "mm"]:
                    if unit in txt:
                        try:
                            if unit == "pulse":
                                current_value = int(txt.replace("pulse", "").strip())
                            else:  # mm
                                unit_value = float(txt.replace("mm", "").strip())
                                current_value = int(unit_value * val2pulse / sense)
                            break
                        except ValueError:
                            continue

            # 現在値が取得できなかった場合はスキップ
            if current_value is None:
                continue

            # センス値を適用した値
            adjusted_value = current_value * sense

            # 軸のユニットに応じて文字色を設定
            lbl_pos = wdict.get("pos_label")
            if lbl_pos:
                # pulse軸は黒、特殊単位軸（mm/deg/mrad）は青色
                if axis_obj.unit in ["mm", "deg", "mrad", "angstroam", "kev"]:
                    lbl_pos.config(fg="blue")  # 特殊単位の軸は青色
                else:
                    lbl_pos.config(fg="black")  # 通常のpulse軸は黒色

            # 表示モードに応じて表示を更新
            if self.unit_var.get() == "pulse":
                # パルス表示モード
                if axis_obj.unit in ["mm", "deg", "mrad", "angstroam", "kev"]:
                    # 特殊単位の軸も標準軸と同様にpulseのみを表示
                    pulse_val = int(adjusted_value * val2pulse)
                    wdict["pos_var"].set(f"{pulse_val} pulse")
                else:
                    # 通常の軸はパルス値のみ表示
                    wdict["pos_var"].set(f"{int(adjusted_value)} pulse")
            else:
                # mm表示モード
                if axis_obj.unit in ["mm", "deg", "mrad", "angstroam", "kev"]:
                    # 特殊単位の軸はその単位で表示
                    if unit_value is None:
                        unit_value = adjusted_value / val2pulse
                    wdict["pos_var"].set(f"{unit_value} {axis_obj.unit}")
                else:
                    # 通常の軸はmm単位に変換して表示
                    mm_val = adjusted_value / val2pulse
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
            st, pos_val, error_flag = fetch_state_and_position(ax)
            if error_flag:
                # エラー状態の軸はログに"ERROR"として記録
                lines_axis.append(f"      - {ax.axis_name}: ERROR")
            else:
                sense_val = ax.sense
                displayed = pos_val * sense_val
                time.sleep(0.1)

                # 軸の単位に応じて表示
                if ax.unit in ["mm", "deg", "mrad", "angstroam", "kev"]:
                    # 特殊単位の軸はその単位で記録
                    lines_axis.append(f"      - {ax.axis_name}: {displayed} {ax.unit}")
                else:
                    # 通常の軸はpulseで記録
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

    # ---------- 非同期ポーリング処理 ----------
    def setup_async_polling(self):
        """非同期ポーリングの初期設定"""
        # 軸ごとの単位情報を保存する辞書を作成
        self.axis_unit_cache = {}

        # bss.configから読み込んだcunit情報を初期設定
        for group in self.config_groups:
            for axis in group.get("axes", []):
                if axis.unit != "pulse":  # pulseでない場合は初期設定
                    self.axis_unit_cache[axis.axis_name] = axis.unit

        # ポーリングスレッドの作成と開始
        self.polling_thread = threading.Thread(target=self.run_async_loop, daemon=True)
        self.polling_thread.start()

    def run_async_loop(self):
        """非同期イベントループを実行するスレッド関数"""
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        # ソケット通信用のロックを作成
        self.socket_lock = asyncio.Lock()
        try:
            self.loop.run_forever()
        finally:
            self.loop.close()

    async def async_fetch_state_and_position(self, axis: Axis):
        """
        排他制御付きの非同期状態・位置取得関数
        1つの軸の通信が完了するまで他の軸の通信をブロックする
        これによりrequest-reply通信の順序が保証される
        """
        # ロックを取得して他の軸の通信をブロック
        async with self.socket_lock:
            # 時間のかかる通信処理をスレッドプールで実行
            return await self.loop.run_in_executor(None, lambda: fetch_state_and_position(axis))

    async def async_fetch_axis_status(self, axis: Axis):
        """
        排他制御付きの非同期ステータス取得関数
        1つの軸の通信が完了するまで他の軸の通信をブロックする
        これによりrequest-reply通信の順序が保証される
        """
        # ロックを取得して他の軸の通信をブロック
        async with self.socket_lock:
            # 時間のかかる通信処理をスレッドプールで実行
            return await self.loop.run_in_executor(None, lambda: fetch_axis_status(axis))

    def on_closing(self):
        """アプリケーション終了時の処理"""
        self.is_shutting_down = True
        # 実行中のタスクをキャンセル
        if self.loop:
            for task in self.polling_tasks.values():
                if not task.done():
                    self.loop.call_soon_threadsafe(task.cancel)
            self.loop.call_soon_threadsafe(self.loop.stop)
        self.root.destroy()

    def start_polling_task(self, axis, expected_pos=None, after_move=False, retry_count=0):
        """軸ごとの非同期ポーリングタスクを開始"""
        # 既存のタスクがあればキャンセル
        axis_name = axis.axis_name
        if axis_name in self.polling_tasks and not self.polling_tasks[axis_name].done():
            self.loop.call_soon_threadsafe(self.polling_tasks[axis_name].cancel)

        # 新しいタスクを作成して開始
        if self.loop:
            # axisのコピーを使用して新しいタスクを作成
            axis_copy = axis.copy()
            future = asyncio.run_coroutine_threadsafe(
                self.poll_axis_async(axis_copy, expected_pos, after_move, retry_count),
                self.loop
            )
            self.polling_tasks[axis_name] = future

    async def poll_axis_async(self, axis, expected_pos=None, after_move=False, retry_count=0):
        """軸の状態と位置を非同期で取得して表示を更新する"""
        axis_name = axis.axis_name

        # シャットダウン中なら処理しない
        if self.is_shutting_down:
            return

        # GUIスレッドでのウィジェット更新
        if axis_name not in self.axis_widgets:
            return

        # エラーが発生している軸はポーリングしない（after_move=True の場合は例外）
        if axis_name in self.error_axes and not after_move:
            return

        # 位置と状態を排他制御付きで取得
        st, pos_int, error_flag = await self.async_fetch_state_and_position(axis)

        # 単位情報が取得できた場合は、ウィジェットとキャッシュに保存
        if not error_flag and axis.unit != "pulse":
            # ウィジェットに保存
            if axis_name in self.axis_widgets:
                self.axis_widgets[axis_name]["unit"] = axis.unit
            # グローバルキャッシュに保存
            self.axis_unit_cache[axis_name] = axis.unit

        # ステータス情報を取得（リミット状態など）
        # statusコマンドが失敗した軸はリミット情報を取得しない
        if axis_name not in self.status_disabled_axes:
            # 排他制御付きでstatusコマンドを実行
            status_success = await self.async_fetch_axis_status(axis)

            # 失敗した場合は以降のポーリングでskipするリストに追加
            if not status_success:
                print(f"[Info] Status command failed for {axis_name}, disabling status polling for this axis")
                self.status_disabled_axes.add(axis_name)
                # リミット情報をクリア
                axis.cw_hard_limit = False
                axis.ccw_hard_limit = False
                axis.cw_soft_limit = False
                axis.ccw_soft_limit = False
                axis.home_position = False
                axis.status_decimal = 0
        else:
            # 以前に失敗したことがある軸はリミット情報をクリア
            axis.cw_hard_limit = False
            axis.ccw_hard_limit = False
            axis.cw_soft_limit = False
            axis.ccw_soft_limit = False
            axis.home_position = False
            axis.status_decimal = 0

        # メインスレッド（GUIスレッド）で実行する必要がある処理
        def update_ui():
            if axis_name not in self.axis_widgets:
                return

            w = self.axis_widgets[axis_name]
            s = axis.sense
            lbl_pos = w["pos_label"]

            # エラー状態の場合は特別処理
            if error_flag:
                w["pos_var"].set("ERROR")
                lbl_pos.config(bg="red")

                # エラー軸リストに追加（再ポーリングしない）
                print(f"[Error] Axis {axis_name} added to error list - will not be polled again")
                self.error_axes.add(axis_name)
                return

            # リミット表示の更新
            limit_labels = w.get("limit_labels", [])
            if len(limit_labels) == 5:
                # cw hard limit, cw soft limit, home, ccw soft limit, ccw hard limit の順
                # リミット状態をビジュアル表示（□/■で表示、リミットはred、homeはblue）
                states = [
                    (axis.cw_hard_limit, "red"),     # CW Hard Limit (index 0)
                    (axis.cw_soft_limit, "red"),     # CW Soft Limit (index 1)
                    (axis.home_position, "blue"),    # Home Position (index 2)
                    (axis.ccw_soft_limit, "red"),    # CCW Soft Limit (index 3)
                    (axis.ccw_hard_limit, "red")     # CCW Hard Limit (index 4)
                ]

                for i, (state, color) in enumerate(states):
                    if state:
                        limit_labels[i].config(text="■", fg=color)
                    else:
                        limit_labels[i].config(text="□", fg="black")

            # 位置表示の更新 (現在の表示モードと軸の単位に応じて表示)
            adjusted_value = pos_int * s  # センス値を適用した値

            # 軸のユニットに応じて文字色を設定
            # pulse軸は黒、特殊単位軸（mm/deg/mrad）は青色
            if axis.unit in ["mm", "deg", "mrad", "angstroam", "kev"]:
                lbl_pos.config(fg="blue")  # 特殊単位の軸は青色
            else:
                lbl_pos.config(fg="black")  # 通常のpulse軸は黒色

            if self.unit_var.get() == "pulse":
                # GUIがパルス表示モードの場合
                if axis.unit in ["mm", "deg", "mrad", "angstroam", "kev"]:
                    # 特殊単位の軸も標準軸と同様にpulseのみを表示
                    pulse_val = int(adjusted_value * axis.val2pulse)
                    w["pos_var"].set(f"{pulse_val} pulse")
                else:
                    # 通常の軸はそのままpulseで表示
                    w["pos_var"].set(f"{int(adjusted_value)} pulse")
            else:
                # GUIがmm表示モードの場合
                if axis.unit in ["mm", "deg", "mrad", "angstroam", "kev"]:
                    # 特殊単位の軸はその単位でそのまま表示
                    w["pos_var"].set(f"{adjusted_value} {axis.unit}")
                else:
                    # 通常の軸はmm単位に変換
                    mm_val = adjusted_value / axis.val2pulse
                    w["pos_var"].set(f"{mm_val:.3f} mm")

            # 状態に応じた背景色の設定（主にエラーと動作中の表示）
            bg_color = self.bg_default[axis_name]

            if st.lower() == "error":
                bg_color = "red"
            elif st.lower() != "inactive":
                bg_color = "yellow"  # 移動中

            lbl_pos.config(bg=bg_color)

        # GUIスレッドで表示更新を実行
        if not self.is_shutting_down:
            self.root.after(0, update_ui)

        # 移動命令直後の特別処理
        if after_move and expected_pos is not None and retry_count < 3:
            if st.lower() == "inactive" and abs(pos_int - expected_pos) > 10:
                # 移動命令直後なのに inactive かつ位置が予想と違う場合
                # → 100ms後に再度確認（最大3回）
                print(f"[Info] Movement command sent but axis {axis_name} reports 'inactive'. Rechecking in 100ms...")
                # 非同期で待機してから再ポーリング
                await asyncio.sleep(0.1)
                if not self.is_shutting_down:
                    await self.poll_axis_async(axis, expected_pos, True, retry_count + 1)
                return

        # 継続的なポーリングの設定
        if not self.is_shutting_down:
            if st.lower() == "error":
                # エラー状態でも5秒後に再ポーリング
                await asyncio.sleep(5.0)
                if not self.is_shutting_down and axis_name not in self.error_axes:
                    self.start_polling_task(axis)
            elif st.lower() != "inactive":
                # 動いている間は1秒ごとに再ポーリング
                await asyncio.sleep(0.3)
                if not self.is_shutting_down:
                    self.start_polling_task(axis)

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


# --- CLIテスト機能 ---
def test_axis(axis_name: str):
    """
    指定された軸の現在の位置とステータスをGUIなしで表示するテスト機能

    Args:
        axis_name: テストする軸の名前
    """
    print(f"テスト対象軸: {axis_name}")
    print("----------------------------------")

    # bss.configを解析して軸情報を取得
    all_axes = parse_bss_config(BSS_CONFIG_PATH)
    found_axis = None

    for axis in all_axes:
        if axis.axis_name == axis_name:
            found_axis = axis
            break

    if not found_axis:
        print(f"エラー: 軸 '{axis_name}' は bss.config に見つかりませんでした。")
        return

    print(f"軸情報: {found_axis}")
    print("----------------------------------")

    # 現在の位置と状態を取得
    try:
        # 軸の位置と状態を取得
        state, position, error_flag = fetch_state_and_position(found_axis)

        if error_flag:
            print("通信エラー: 軸の位置と状態を取得できませんでした。")
            print("※ この軸はGUIモードでは再ポーリングされなくなります。")
        else:
            # センス値を適用して表示
            sense = found_axis.sense
            adjusted_position = position * sense

            print(f"現在の状態: {state}")
            print(f"位置 (pulse): {adjusted_position} pulse")

            # mmに変換して表示
            if found_axis.val2pulse > 0:
                mm_position = adjusted_position / found_axis.val2pulse
                print(f"位置 (mm): {mm_position:.3f} mm")

            # ステータス情報を取得
            status_success = fetch_axis_status(found_axis)

            if status_success:
                print("----------------------------------")
                print("リミット状態:")
                print(f"CW Hard Limit: {'あり' if found_axis.cw_hard_limit else 'なし'}")
                print(f"CW Soft Limit: {'あり' if found_axis.cw_soft_limit else 'なし'}")
                print(f"Home Position: {'あり' if found_axis.home_position else 'なし'}")
                print(f"CCW Soft Limit: {'あり' if found_axis.ccw_soft_limit else 'なし'}")
                print(f"CCW Hard Limit: {'あり' if found_axis.ccw_hard_limit else 'なし'}")
                print(f"ステータス値 (10進数): {found_axis.status_decimal}")
            else:
                print("----------------------------------")
                print("警告: リミット状態を取得できませんでした。")
                print("※ このエラーが継続する場合、GUI モードではこの軸のステータスポーリングは無効化されます。")

    except Exception as e:
        print(f"エラー: {e}")


# --- コマンドライン引数の解析 ---
def parse_args():
    """
    コマンドライン引数を解析する

    Returns:
        dict: 解析された引数とその値
    """
    # デフォルト：GUIモード
    args = {
        "mode": "gui",
        "config_file": None,
        "test_axis": None
    }

    i = 1
    while i < len(sys.argv):
        arg = sys.argv[i]

        if arg == "--test-axis" and i + 1 < len(sys.argv):
            args["mode"] = "test"
            args["test_axis"] = sys.argv[i + 1]
            i += 2
        elif not arg.startswith("--") and args["config_file"] is None:
            # 設定ファイルパス
            args["config_file"] = arg
            i += 1
        else:
            i += 1

    return args


# --- メイン関数 ---
def main():
    """
    起動時に default_axis.yaml と user_axis.yaml の両方からグループ情報を読み込み、
    bss.config から全軸情報（"all" グループ）および空の "favorite" グループを追加して GUI に渡す。

    コマンドライン引数 --test-axis {axis_name} が指定された場合は、
    GUIを起動せずに指定された軸の現在の位置とステータスを表示する。
    """
    args = parse_args()

    # テストモード
    if args["mode"] == "test" and args["test_axis"]:
        test_axis(args["test_axis"])
        return

    # 通常のGUIモード
    config_groups = load_all_configs()
    all_axes = parse_bss_config(BSS_CONFIG_PATH)
    config_groups.append({"name": "all", "axes": all_axes})
    config_groups.append({"name": "favorite", "axes": []})
    root = tk.Tk()
    app = AxisToolApp(root, config_groups)
    root.mainloop()


if __name__ == "__main__":
    main()

