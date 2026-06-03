import gzip
import json
import os
import sys
import subprocess
import threading
import urllib.parse
import urllib.request
import zlib
import shutil
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
import tempfile
import zipfile
import re
try:
    import winreg
except Exception:
    winreg = None

try:
    import webview
except ImportError:
    webview = None

try:
    from yt_dlp import YoutubeDL
except ImportError:
    YoutubeDL = None


USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

SEARCH_API = (
    "https://api.bilibili.com/x/web-interface/search/all/v2?"
    "search_type=video&keyword={keyword}&order=totalrank&page={page}"
)

DEFAULT_HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "Accept-Language": "zh-CN,zh;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Referer": "https://www.bilibili.com/",
    "Origin": "https://www.bilibili.com",
    "Connection": "keep-alive",
    "X-Requested-With": "XMLHttpRequest",
    "Sec-Fetch-Site": "same-site",
    "Sec-Fetch-Mode": "cors",
    "Sec-Fetch-Dest": "empty",
}


def find_ffmpeg():
    """Return the path to ffmpeg executable if installed, otherwise None."""
    env_path = os.environ.get('FFMPEG_LOCAL')
    if env_path and os.path.exists(env_path):
        return env_path

    for name in ('ffmpeg', 'ffmpeg.exe'):
        result = shutil.which(name)
        if result:
            return result

    if sys.platform.startswith('win'):
        exe_dir = os.path.dirname(sys.executable)
        bundle_dir = getattr(sys, '_MEIPASS', None)
        possible_dirs = [
            env_path,
            os.path.join(exe_dir, 'ffmpeg.exe'),
            os.path.join(exe_dir, 'ffprobe.exe'),
            os.path.join(bundle_dir or '', 'ffmpeg.exe'),
            os.path.join(bundle_dir or '', 'ffprobe.exe'),
            os.path.join(os.environ.get('ProgramFiles', ''), 'ffmpeg', 'bin', 'ffmpeg.exe'),
            os.path.join(os.environ.get('ProgramFiles(x86)', ''), 'ffmpeg', 'bin', 'ffmpeg.exe'),
            os.path.join(os.environ.get('ProgramW6432', ''), 'ffmpeg', 'bin', 'ffmpeg.exe'),
            os.path.join(os.environ.get('USERPROFILE', ''), 'ffmpeg', 'bin', 'ffmpeg.exe'),
            os.path.join(os.path.dirname(__file__), 'ffmpeg.exe'),
        ]
        for path in possible_dirs:
            if path and os.path.exists(path):
                return path

    return None


def request_json(url, timeout=15):
    request = urllib.request.Request(url, headers=DEFAULT_HEADERS)
    with urllib.request.urlopen(request, timeout=timeout) as response:
        raw = response.read()
        encoding = response.headers.get("Content-Encoding", "").lower()
        if encoding == "gzip":
            raw = gzip.decompress(raw)
        elif encoding == "deflate":
            try:
                raw = zlib.decompress(raw)
            except zlib.error:
                raw = zlib.decompress(raw, -zlib.MAX_WBITS)
        elif encoding == "br":
            try:
                import brotli
                raw = brotli.decompress(raw)
            except ImportError:
                raise RuntimeError(
                    "服务器返回了 brotli 压缩结果，请安装 brotli: python -m pip install brotli"
                )
        charset = response.headers.get_content_charset("utf-8")
        text = raw.decode(charset, errors="replace")
        return json.loads(text)


def search_bilibili(keyword, page=1):
    quoted = urllib.parse.quote(keyword)
    url = SEARCH_API.format(keyword=quoted, page=page)
    data = request_json(url)
    if data.get("code") != 0:
        raise RuntimeError(f"搜索失败: {data.get('message', 'unknown')}")

    results = []
    for section in data.get("data", {}).get("result", []):
        if section.get("result_type") != "video":
            continue
        for item in section.get("data", []):
            title = _strip_html(item.get("title", ""))
            bvid = item.get("bvid") or item.get("id") or item.get("aid")
            results.append(
                {
                    "title": title,
                    "author": item.get("author", "未知"),
                    "duration": item.get("duration", "--:--"),
                    "bvid": bvid,
                    "url": f"https://www.bilibili.com/video/{bvid}" if bvid else item.get("arcurl", ""),
                    "view": item.get("play", item.get("view", 0)),
                }
            )
        break
    return results


def _strip_html(text):
    return text.replace("<em class=\"keyword\">", "").replace("</em>", "")


def download_video(video_url, output_dir, keep_separate=False, progress_callback=None):
    if YoutubeDL is None:
        raise RuntimeError(
            "未安装 yt-dlp。请运行：python -m pip install yt-dlp -i https://mirrors.aliyun.com/pypi/simple/"
        )

    output_tmpl = os.path.join(output_dir, "%(title)s [%(id)s].%(ext)s")

    # 先获取格式信息，优先选择单文件格式，避免 ffmpeg 合并
    info_opts = {
        "quiet": True,
        "no_warnings": True,
        "cachedir": False,
        "skip_download": True,
        "http_headers": {"User-Agent": USER_AGENT},
    }
    with YoutubeDL(info_opts) as ydl_info:
        info = ydl_info.extract_info(video_url, download=False)

    formats = info.get("formats") or []
    if not formats and info.get("_type") == "playlist":
        first_entry = next((entry for entry in info.get("entries", []) if entry), None)
        if first_entry:
            formats = first_entry.get("formats") or []

    combined_formats = [
        f for f in formats
        if f.get("vcodec") not in (None, "none") and f.get("acodec") not in (None, "none")
    ]

    def format_score(f):
        score = (f.get("tbr") or 0) * 1000 + (f.get("height") or 0) * 10
        if f.get("ext") == "mp4":
            score += 100000
        return score

    ffmpeg_path = find_ffmpeg()
    ffmpeg_available = bool(ffmpeg_path)

    if combined_formats and not keep_separate:
        selected_format = max(combined_formats, key=format_score).get("format_id")
    elif keep_separate:
        selected_format = "bestvideo+bestaudio/best" if ffmpeg_available else "bestvideo"
    else:
        if not ffmpeg_available:
            raise RuntimeError(
                "当前视频没有可用的单文件格式，单文件下载需要安装 ffmpeg。"
                "请安装 ffmpeg 并重试，或选择“保留分离音视频”模式。"
            )
        selected_format = "bestvideo+bestaudio/best"

    ydl_opts = {
        "format": selected_format,
        "outtmpl": output_tmpl,
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "cachedir": False,
        "noprogress": True,
        "http_headers": {"User-Agent": USER_AGENT},
        "no_color": True,
        "cookiefile": None,
    }
    if keep_separate:
        ydl_opts["keepvideo"] = True

    class ProgressHook:
        def __init__(self, callback):
            self.callback = callback

        def __call__(self, d):
            if self.callback is None:
                return
            status = d.get("status")
            if status == "downloading":
                self.callback(d.get("downloaded_bytes"), d.get("total_bytes"))
            elif status == "finished":
                self.callback(d.get("total_bytes"), d.get("total_bytes"))

    if progress_callback is not None:
        ydl_opts["progress_hooks"] = [ProgressHook(progress_callback)]

    ffmpeg_path = find_ffmpeg()
    if ffmpeg_path:
        ydl_opts['ffmpeg_location'] = ffmpeg_path

    def run_ydl(opts):
        with YoutubeDL(opts) as ydl:
            # 先获取一次 info 来验证所请求的 format 在当前环境中是否可用
            info_check = None
            try:
                info_check = ydl.extract_info(video_url, download=False)
            except Exception:
                # 如果获取格式信息失败，继续让 yt-dlp 在下载阶段尝试并抛出原始错误
                pass

            if info_check:
                fmts = info_check.get("formats") or []
                available_ids = {str(f.get("format_id")) for f in fmts if f.get("format_id")}
                requested = str(opts.get("format"))

                valid = False
                if not requested or requested == 'None':
                    valid = True
                elif str(requested) in ("best", "worst", "bestvideo", "bestaudio"):
                    valid = True
                elif requested in available_ids:
                    valid = True
                else:
                    # 尝试把表达式拆分开并检查部件是否都存在于 available_ids 或已知特殊格式符号
                    parts = [p for p in re.split(r"[+/]", str(requested)) if p]
                    valid_tokens = {
                        "best", "worst", "bestvideo", "bestaudio",
                        "worstvideo", "worstaudio"
                    }
                    if all(p in available_ids or p in valid_tokens for p in parts):
                        valid = True

                if not valid:
                    # 重新选择一个可用的单文件合并格式（优先 mp4）
                    muxed = [f for f in fmts if f.get("vcodec") not in (None, "none") and f.get("acodec") not in (None, "none")]
                    if muxed:
                        best_mux = max(muxed, key=format_score)
                        opts["format"] = best_mux.get("format_id")
                    else:
                        # 没有合并格式，退回到通用 best
                        opts["format"] = "best"

            info = ydl.extract_info(video_url, download=True)
            if info is None:
                raise RuntimeError("下载失败，未获取视频信息。")
            if info.get("_type") == "playlist":
                info = info.get("entries", [])[0]
                if info is None:
                    raise RuntimeError("无法处理播放列表。请选择单个视频。")
            return ydl.prepare_filename(info)

    def download_separate_streams():
        video_template = os.path.join(output_dir, "%(title)s [%(id)s].video.%(ext)s")
        audio_template = os.path.join(output_dir, "%(title)s [%(id)s].audio.%(ext)s")

        video_opts = dict(ydl_opts)
        video_opts.pop("keepvideo", None)
        video_opts["format"] = "bestvideo"
        video_opts["outtmpl"] = video_template

        audio_opts = dict(ydl_opts)
        audio_opts.pop("keepvideo", None)
        audio_opts["format"] = "bestaudio"
        audio_opts["outtmpl"] = audio_template

        video_file = run_ydl(video_opts)
        audio_file = run_ydl(audio_opts)
        return video_file, audio_file

    try:
        if keep_separate and not ffmpeg_available:
            return download_separate_streams()
        return run_ydl(ydl_opts)
    except Exception as first_exc:
        # 如果首次失败，且我们没有使用通用 best，则尝试回退到 best 并重试
        if selected_format != "best":
            ydl_opts["format"] = "best"
            try:
                return run_ydl(ydl_opts)
            except Exception as second_exc:
                # 收集可用格式并返回友好错误信息
                try:
                    info_list_opts = {
                        "quiet": True,
                        "no_warnings": True,
                        "cachedir": False,
                        "skip_download": True,
                        "http_headers": {"User-Agent": USER_AGENT},
                    }
                    with YoutubeDL(info_list_opts) as ydl_list:
                        info_for_list = ydl_list.extract_info(video_url, download=False)
                        fmts = info_for_list.get("formats") or []
                        ids = [f.get("format_id") for f in fmts if f.get("format_id")]
                        sample = ", ".join(ids[:20]) if ids else "(无可用格式)"
                        msg = (
                            f"下载失败: {second_exc}\n可用格式示例: {sample}\n"
                            f"可使用 --list-formats 查看完整格式列表"
                        )
                except Exception:
                    msg = f"下载失败: {second_exc}"
                raise RuntimeError(msg) from second_exc
        else:
            # 已经使用 best 仍失败，尝试列出可用格式帮助诊断
            try:
                info_list_opts = {
                    "quiet": True,
                    "no_warnings": True,
                    "cachedir": False,
                    "skip_download": True,
                    "http_headers": {"User-Agent": USER_AGENT},
                }
                with YoutubeDL(info_list_opts) as ydl_list:
                    info_for_list = ydl_list.extract_info(video_url, download=False)
                    fmts = info_for_list.get("formats") or []
                    ids = [f.get("format_id") for f in fmts if f.get("format_id")]
                    sample = ", ".join(ids[:20]) if ids else "(无可用格式)"
                    msg = (
                        f"下载失败: {first_exc}\n可用格式示例: {sample}\n"
                        f"可使用 --list-formats 查看完整格式列表"
                    )
            except Exception:
                raise first_exc
            raise RuntimeError(msg) from first_exc


def ensure_ffmpeg_local(dest=None, url=None):
    """下载并安装静态 ffmpeg 到用户目录（默认：%USERPROFILE%\\ffmpeg_local），并尝试将该目录加入用户 PATH。
    返回安装目录路径或抛出异常。
    """
    if dest is None:
        dest = os.path.join(os.path.expanduser("~"), "ffmpeg_local")
    if url is None:
        url = 'https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip'

    # 下载到多个候选位置以应对临时目录写入受限的情况
    candidates = [
        os.path.join(tempfile.gettempdir(), 'ffmpeg.zip'),
        os.path.join(os.path.expanduser('~'), 'Downloads', 'ffmpeg.zip'),
        os.path.join(os.path.dirname(__file__), 'ffmpeg.zip'),
    ]
    out = None
    for cand in candidates:
        try:
            # 尝试删除已存在的文件以避免 PermissionError
            if os.path.exists(cand):
                try:
                    os.remove(cand)
                except Exception:
                    pass
            urllib.request.urlretrieve(url, cand)
            out = cand
            break
        except PermissionError as e:
            continue
        except Exception:
            continue
    if out is None:
        raise RuntimeError("无法将 ffmpeg 下载到任何候选位置（权限或网络问题）。")

    unpack = os.path.join(tempfile.gettempdir(), 'ffmpeg_unpack')

    try:
        if os.path.exists(unpack):
            try:
                shutil.rmtree(unpack)
            except Exception:
                pass
        with zipfile.ZipFile(out, 'r') as z:
            z.extractall(unpack)
    except Exception as e:
        raise RuntimeError(f"解压 ffmpeg 失败: {e}")

    ffmpeg_exe = None
    for root, dirs, files in os.walk(unpack):
        if 'ffmpeg.exe' in files:
            ffmpeg_exe = os.path.join(root, 'ffmpeg.exe')
            break

    if not ffmpeg_exe:
        raise RuntimeError("在下载包中未找到 ffmpeg.exe")

    src_dir = os.path.dirname(ffmpeg_exe)
    try:
        os.makedirs(dest, exist_ok=True)
        # 复制 bin 及相关文件
        for name in os.listdir(src_dir):
            s = os.path.join(src_dir, name)
            d = os.path.join(dest, name)
            if os.path.isdir(s):
                if os.path.exists(d):
                    shutil.rmtree(d)
                shutil.copytree(s, d)
            else:
                shutil.copy2(s, d)
    except Exception as e:
        raise RuntimeError(f"复制 ffmpeg 文件失败: {e}")

    # 尝试将 dest 加入当前会话 PATH
    os.environ['PATH'] = os.environ.get('PATH', '') + os.pathsep + dest

    # 尝试更新用户 PATH（写入注册表），以便新开终端可用
    if winreg is not None:
        try:
            try:
                with winreg.OpenKey(winreg.HKEY_CURRENT_USER, 'Environment', 0, winreg.KEY_READ) as key:
                    try:
                        user_path, _ = winreg.QueryValueEx(key, 'Path')
                    except FileNotFoundError:
                        user_path = ''
            except Exception:
                user_path = ''

            if dest not in user_path:
                new = user_path + ';' + dest if user_path else dest
                with winreg.OpenKey(winreg.HKEY_CURRENT_USER, 'Environment', 0, winreg.KEY_SET_VALUE) as key:
                    winreg.SetValueEx(key, 'Path', 0, winreg.REG_EXPAND_SZ, new)
        except Exception:
            # 不要阻塞安装流程，仅记录不抛出
            pass

    # 最后验证
    if shutil.which('ffmpeg'):
        return dest
    else:
        # 仍未在 PATH 中找到，可直接返回 dest（调用方可自行处理）
        return dest


def open_with_system_player(file_path):
    if not os.path.isfile(file_path):
        raise FileNotFoundError(file_path)
    if sys.platform.startswith("win"):
        os.startfile(file_path)
    elif sys.platform.startswith("darwin"):
        subprocess.Popen(["open", file_path])
    else:
        subprocess.Popen(["xdg-open", file_path])


class BilibiliApp:
    def __init__(self, root):
        self.root = root
        self.root.title("BiliBili视频下载器")

        screen_w = self.root.winfo_screenwidth()
        screen_h = self.root.winfo_screenheight()
        width = min(900, max(760, screen_w - 80))
        height = min(650, max(520, screen_h - 120))
        x = max(0, (screen_w - width) // 2)
        y = max(0, (screen_h - height) // 2)
        self.root.geometry(f"{width}x{height}+{x}+{y}")
        self.root.resizable(True, True)

        self.videos = []
        self.selected_index = None
        self.download_path = os.path.abspath(os.getcwd())
        self.local_file_path = None

        self._build_ui()

    def _build_ui(self):
        frame = ttk.Frame(self.root, padding=12)
        frame.pack(fill=tk.BOTH, expand=True)
        self.root.minsize(900, 650)

        search_frame = ttk.Frame(frame)
        search_frame.pack(fill=tk.X, pady=(0, 10))

        ttk.Label(search_frame, text="搜索 B 站 视频：").pack(side=tk.LEFT)
        self.keyword_var = tk.StringVar()
        ttk.Entry(search_frame, textvariable=self.keyword_var, width=48).pack(side=tk.LEFT, padx=(8, 4))
        ttk.Button(search_frame, text="搜索", command=self.on_search).pack(side=tk.LEFT, padx=(4, 4))
        ttk.Button(search_frame, text="清空", command=self.on_clear).pack(side=tk.LEFT)

        results_frame = ttk.Frame(frame)
        results_frame.pack(fill=tk.BOTH, expand=True)

        self.results_listbox = tk.Listbox(
            results_frame,
            height=18,
            activestyle="dotbox",
            selectmode=tk.SINGLE,
            font=("Segoe UI", 10),
        )
        self.results_listbox.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.results_listbox.bind("<<ListboxSelect>>", self.on_result_select)

        scrollbar = ttk.Scrollbar(results_frame, orient=tk.VERTICAL, command=self.results_listbox.yview)
        scrollbar.pack(side=tk.LEFT, fill=tk.Y)
        self.results_listbox.config(yscrollcommand=scrollbar.set)

        details_frame = ttk.Frame(frame, padding=(10, 0, 0, 0))
        details_frame.pack(fill=tk.X, pady=(10, 0))

        self.details_text = tk.Text(details_frame, height=9, wrap=tk.WORD, state=tk.DISABLED, font=("Segoe UI", 10))
        self.details_text.pack(fill=tk.BOTH, expand=True)

        action_frame = ttk.Frame(frame)
        action_frame.pack(fill=tk.X, pady=(10, 0))

        self.path_var = tk.StringVar(value=self.download_path)
        ttk.Label(action_frame, text="保存目录：").pack(side=tk.LEFT)
        ttk.Entry(action_frame, textvariable=self.path_var, width=36).pack(side=tk.LEFT, padx=(6, 4))
        ttk.Button(action_frame, text="选择目录", command=self.choose_output_folder).pack(side=tk.LEFT)

        self.download_mode_var = tk.StringVar(value="合并下载（单文件）")
        ttk.Label(action_frame, text="下载模式：").pack(side=tk.LEFT, padx=(12, 0))
        self.download_mode_combo = ttk.Combobox(
            action_frame,
            textvariable=self.download_mode_var,
            values=["合并下载（单文件）", "保留分离音视频"],
            state="readonly",
            width=16,
        )
        self.download_mode_combo.pack(side=tk.LEFT, padx=(6, 0))

        button_frame = ttk.Frame(frame)
        button_frame.pack(fill=tk.X, pady=(10, 0))

        ttk.Button(button_frame, text="下载选中视频", command=self.on_download).pack(side=tk.LEFT)
        ttk.Button(button_frame, text="预览选中视频", command=self.on_preview).pack(side=tk.LEFT, padx=(10, 0))
        ttk.Button(button_frame, text="播放本地视频", command=self.on_play_local).pack(side=tk.LEFT, padx=(10, 0))
        ttk.Button(button_frame, text="打开下载目录", command=self.open_download_folder).pack(side=tk.LEFT, padx=(10, 0))

        status_frame = ttk.Frame(frame)
        status_frame.pack(fill=tk.X, pady=(10, 0))

        self.status_var = tk.StringVar(value="准备就绪。搜索视频后可下载和播放。")
        ttk.Label(status_frame, textvariable=self.status_var, foreground="#1a73e8").pack(fill=tk.X)

        self.progress_var = tk.DoubleVar(value=0.0)
        self.progress_text_var = tk.StringVar(value="")
        self.progress_bar = ttk.Progressbar(status_frame, variable=self.progress_var, maximum=100, mode="determinate")
        self.progress_bar.pack(fill=tk.X, pady=(6, 0))
        self.progress_running = False
        ttk.Label(status_frame, textvariable=self.progress_text_var, foreground="#333333").pack(fill=tk.X)

    def on_search(self):
        keyword = self.keyword_var.get().strip()
        if not keyword:
            messagebox.showwarning("请输入关键词", "请在搜索框中输入视频关键词。")
            return

        self.status_var.set("正在搜索，请稍候...")
        self.root.update_idletasks()
        try:
            self.videos = search_bilibili(keyword)
        except Exception as exc:
            messagebox.showerror("搜索失败", str(exc))
            self.status_var.set("搜索失败，请检查网络或关键字。")
            return

        self.results_listbox.delete(0, tk.END)
        if not self.videos:
            self.status_var.set("未找到匹配视频。")
            return

        for item in self.videos:
            title = item["title"]
            author = item["author"]
            duration = item["duration"]
            self.results_listbox.insert(tk.END, f"{title}  [{duration}]  作者：{author}")

        self.status_var.set(f"搜索完成：{len(self.videos)} 个结果。请选择一个视频下载。")

    def on_clear(self):
        self.keyword_var.set("")
        self.results_listbox.delete(0, tk.END)
        self._set_details_text("")
        self.videos = []
        self.selected_index = None
        self.status_var.set("已清空搜索结果。")

    def on_result_select(self, event=None):
        selection = self.results_listbox.curselection()
        if not selection:
            return
        index = selection[0]
        self.selected_index = index
        video = self.videos[index]
        text = (
            f"标题：{video['title']}\n"
            f"作者：{video['author']}\n"
            f"时长：{video['duration']}\n"
            f"播放地址：{video['url']}\n"
            f"播放量：{video['view']}\n"
            "\n点击“下载选中视频”即可保存至本地。"
        )
        self._set_details_text(text)

    def _set_details_text(self, text):
        self.details_text.config(state=tk.NORMAL)
        self.details_text.delete("1.0", tk.END)
        self.details_text.insert(tk.END, text)
        self.details_text.config(state=tk.DISABLED)

    def choose_output_folder(self):
        folder = filedialog.askdirectory(initialdir=self.download_path)
        if folder:
            self.download_path = folder
            self.path_var.set(folder)

    def on_download(self):
        if self.selected_index is None or self.selected_index >= len(self.videos):
            messagebox.showwarning("未选择视频", "请先从搜索结果中选择一个视频。")
            return

        if YoutubeDL is None:
            messagebox.showerror(
                "缺少依赖",
                "请先安装 yt-dlp：python -m pip install yt-dlp"
            )
            return

        output_dir = self.path_var.get().strip() or self.download_path
        if not os.path.isdir(output_dir):
            messagebox.showwarning("目录不存在", "请选择一个有效的保存目录。")
            return
        video = self.videos[self.selected_index]
        keep_separate = self.download_mode_var.get() == "保留分离音视频"
        ffmpeg_path = find_ffmpeg()
        if ffmpeg_path:
            os.environ['FFMPEG_LOCAL'] = ffmpeg_path

        if keep_separate and not ffmpeg_path:
            self.status_var.set("未检测到 ffmpeg，将以保留分离音视频模式下载独立音视频流。")
            self.progress_text_var.set("正在准备下载分离视频和音频...")

        self.status_var.set(f"开始下载：{video['title']} ...")
        self.progress_bar.config(mode="determinate", maximum=100)
        if self.progress_running:
            self.progress_bar.stop()
            self.progress_running = False
        self.progress_var.set(0.0)
        self.progress_text_var.set("正在准备下载...")
        self.root.update_idletasks()

        def on_success(saved_file):
            if self.progress_running:
                self.progress_bar.stop()
                self.progress_running = False
            if isinstance(saved_file, (list, tuple)):
                self.local_file_path = saved_file[0]
                saved_text = f"视频：{saved_file[0]}\n音频：{saved_file[1]}"
            else:
                self.local_file_path = saved_file
                saved_text = str(saved_file)
            self.status_var.set("下载完成")
            self.progress_var.set(100.0)
            self.progress_text_var.set("下载完成")
            messagebox.showinfo("下载完成", f"已保存到：\n{saved_text}")

        def on_failure(exc):
            if self.progress_running:
                self.progress_bar.stop()
                self.progress_running = False
            self.status_var.set("下载失败，请重试。")
            self.progress_var.set(0.0)
            self.progress_text_var.set("")
            messagebox.showerror("下载失败", str(exc))

        def update_progress(downloaded, total):
            if total and total > 0:
                if self.progress_bar['mode'] != 'determinate':
                    self.progress_bar.stop()
                    self.progress_bar.config(mode='determinate', maximum=100)
                    self.progress_running = False
                percent = min(100.0, downloaded / total * 100)
                self.progress_var.set(percent)
                self.progress_text_var.set(
                    f"下载中：{percent:.1f}% ({downloaded // 1024}KB / {total // 1024}KB)"
                )
            else:
                if self.progress_bar['mode'] != 'indeterminate':
                    self.progress_bar.config(mode='indeterminate')
                if not self.progress_running:
                    self.progress_bar.start(10)
                    self.progress_running = True
                self.progress_text_var.set(f"已下载 {downloaded // 1024}KB")
            self.root.update_idletasks()

        def run_download():
            try:
                saved_file = download_video(
                    video["url"], output_dir, keep_separate=keep_separate, progress_callback=lambda d, t: self.root.after(0, lambda: update_progress(d, t))
                )
                self.root.after(0, lambda: on_success(saved_file))
            except Exception as exc:
                self.root.after(0, lambda exc=exc: on_failure(exc))

        threading.Thread(target=run_download, daemon=True).start()

    def on_preview(self):
        if self.selected_index is None or self.selected_index >= len(self.videos):
            messagebox.showwarning("未选择视频", "请先从搜索结果中选择一个视频进行预览。")
            return

        if webview is None:
            messagebox.showinfo(
                "缺少依赖",
                "缺少依赖：pywebview。请运行：python -m pip install pywebview -i https://mirrors.aliyun.com/pypi/simple/"
            )
            return

        video = self.videos[self.selected_index]
        self.status_var.set("正在打开预览窗口...")
        self.root.update_idletasks()
        # pywebview 必须在主线程中运行；为避免与 Tkinter 主循环冲突，
        # 在子进程中启动一个独立的 Python 进程来创建并运行 webview 窗口。
        try:
            script = (
                "import webview, sys\n"
                f"webview.create_window({video['title']!r}, {video['url']!r})\n"
                "webview.start()\n"
            )
            subprocess.Popen([sys.executable, "-c", script], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception as exc:
            messagebox.showerror("预览失败", str(exc))
            self.status_var.set("预览失败。")

    

    def on_play_local(self):
        if not self.local_file_path or not os.path.isfile(self.local_file_path):
            file_path = filedialog.askopenfilename(
                title="选择本地视频文件",
                filetypes=[("视频文件", "*.mp4 *.mkv *.flv *.webm *.ts *.avi"), ("所有文件", "*")],
            )
            if not file_path:
                return
            self.local_file_path = file_path

        try:
            open_with_system_player(self.local_file_path)
            self.status_var.set(f"正在使用系统默认播放器播放：{self.local_file_path}")
        except Exception as exc:
            messagebox.showerror("播放失败", str(exc))
            self.status_var.set("播放失败，请检查视频文件格式。")

    def open_download_folder(self):
        folder = self.path_var.get().strip() or self.download_path
        if not os.path.isdir(folder):
            folder = os.path.abspath(os.getcwd())
        try:
            if sys.platform.startswith("win"):
                os.startfile(folder)
            elif sys.platform.startswith("darwin"):
                subprocess.Popen(["open", folder])
            else:
                subprocess.Popen(["xdg-open", folder])
        except Exception:
            messagebox.showwarning("打开失败", "无法打开下载目录，请手动进入保存目录。")


def main():
    root = tk.Tk()
    app = BilibiliApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
