import os
import sys
import tkinter as tk
from tkinter import filedialog, messagebox

try:
    import qrcode
    from PIL import Image, ImageTk
except ImportError:
    message = (
        "缺少依赖库：qrcode 和/或 Pillow。\n"
        "请先安装依赖：pip install qrcode[pil] pillow"
    )
    raise ImportError(message)


class QRCodeGeneratorApp:
    def __init__(self, root):
        self.root = root
        self.root.title("二维码生成器")
        self.root.geometry("720x720")
        self.root.resizable(True, True)

        self._build_ui()
        self.image = None
        self.photo_image = None

    def _build_ui(self):
        frame = tk.Frame(self.root, padx=16, pady=16)
        frame.pack(fill=tk.BOTH, expand=True)

        label = tk.Label(frame, text="请输入要生成二维码的文本或链接：", font=("Arial", 12))
        label.grid(row=0, column=0, columnspan=2, sticky="w")

        self.text_input = tk.Text(frame, width=52, height=6, font=("Arial", 11))
        self.text_input.grid(row=1, column=0, columnspan=2, pady=(8, 12), sticky="we")

        size_label = tk.Label(frame, text="二维码尺寸 (像素)：", font=("Arial", 11))
        size_label.grid(row=2, column=0, sticky="w")
        self.size_var = tk.IntVar(value=360)
        size_entry = tk.Entry(frame, textvariable=self.size_var, width=10, font=("Arial", 11))
        size_entry.grid(row=2, column=1, sticky="e")

        fg_label = tk.Label(frame, text="前景色：", font=("Arial", 11))
        fg_label.grid(row=3, column=0, sticky="w", pady=(8, 0))
        self.fg_var = tk.StringVar(value="#000000")
        fg_entry = tk.Entry(frame, textvariable=self.fg_var, width=10, font=("Arial", 11))
        fg_entry.grid(row=3, column=1, sticky="e", pady=(8, 0))

        bg_label = tk.Label(frame, text="背景色：", font=("Arial", 11))
        bg_label.grid(row=4, column=0, sticky="w", pady=(8, 0))
        self.bg_var = tk.StringVar(value="#ffffff")
        bg_entry = tk.Entry(frame, textvariable=self.bg_var, width=10, font=("Arial", 11))
        bg_entry.grid(row=4, column=1, sticky="e", pady=(8, 0))

        generate_button = tk.Button(frame, text="生成二维码", command=self.generate_qr, width=14, font=("Arial", 11, "bold"))
        generate_button.grid(row=5, column=0, pady=(20, 4), sticky="we")

        save_button = tk.Button(frame, text="另存为图片", command=self.save_qr, width=14, font=("Arial", 11, "bold"))
        save_button.grid(row=5, column=1, pady=(20, 4), sticky="we")

        preview_label = tk.Label(frame, text="二维码预览：", font=("Arial", 12))
        preview_label.grid(row=6, column=0, columnspan=2, sticky="w", pady=(16, 8))

        self.preview_size = 360
        self.preview_canvas = tk.Canvas(frame, width=self.preview_size, height=self.preview_size, bg="#f2f2f2", bd=1, relief=tk.SOLID)
        self.preview_canvas.grid(row=7, column=0, columnspan=2)

        self.status_var = tk.StringVar(value="准备生成二维码")
        status_label = tk.Label(frame, textvariable=self.status_var, anchor="w", font=("Arial", 10), fg="#333333")
        status_label.grid(row=8, column=0, columnspan=2, sticky="we", pady=(12, 0))

    def generate_qr(self):
        text = self.text_input.get("1.0", tk.END).strip()
        if not text:
            messagebox.showwarning("输入为空", "请输入要转换为二维码的文本或链接。")
            return

        size = self.size_var.get()
        if size <= 0:
            messagebox.showwarning("尺寸错误", "二维码尺寸应为大于 0 的整数。")
            return

        fg_color = self.fg_var.get().strip() or "#000000"
        bg_color = self.bg_var.get().strip() or "#ffffff"

        try:
            qr = qrcode.QRCode(
                version=None,
                error_correction=qrcode.constants.ERROR_CORRECT_M,
                box_size=10,
                border=4,
            )
            qr.add_data(text)
            qr.make(fit=True)
            img = qr.make_image(fill_color=fg_color, back_color=bg_color).convert("RGB")
            img = img.resize((size, size), Image.Resampling.LANCZOS)
        except Exception as exc:
            messagebox.showerror("生成失败", f"二维码生成出错：{exc}")
            return

        self.image = img
        self._update_preview(img)
        self.status_var.set("二维码生成成功，可点击“另存为图片”保存。")

    def _update_preview(self, image: Image.Image):
        self.photo_image = ImageTk.PhotoImage(image)
        self.preview_canvas.delete("all")
        self.preview_canvas.create_image(180, 180, image=self.photo_image)

    def save_qr(self):
        if self.image is None:
            messagebox.showwarning("未生成二维码", "请先生成二维码，再保存为图片。")
            return

        default_name = "qrcode.png"
        file_path = filedialog.asksaveasfilename(
            defaultextension=".png",
            filetypes=[("PNG 图像", "*.png"), ("JPEG 图像", "*.jpg;*.jpeg"), ("所有文件", "*.*")],
            initialfile=default_name,
            title="保存二维码图片",
        )
        if not file_path:
            return

        try:
            self.image.save(file_path)
            self.status_var.set(f"已保存：{os.path.basename(file_path)}")
            messagebox.showinfo("保存成功", f"二维码已保存到：{file_path}")
        except Exception as exc:
            messagebox.showerror("保存失败", f"保存二维码图片时出错：{exc}")


def main():
    root = tk.Tk()
    app = QRCodeGeneratorApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
