# pdf2scan

## 项目介绍

需要扫描件时，传统流程往往要先打印文件，再用扫描仪处理；手边没有打印机时尤其不便。pdf2scan 省去打印步骤，直接将 PDF、JPG、PNG 等文件转换为扫描件风格的 PDF。默认保留彩色，也可按需生成黑白版本；处理照片时还会尝试识别纸张、矫正透视并提亮阴影。

## 效果展示

下图使用四张原 JPEG。每行对应一张照片，从左到右依次是**原图、pdf2scan 彩色输出、pdf2scan 黑白输出**。

![四组原图与 pdf2scan 彩色、黑白效果对比](assets/showcase-original-color-grayscale.jpg)

## 安装

将整个 `pdf2scan/` 文件夹放入 Codex 的 `~/.agents/skills/` 或 Claude Code 的 `~/.claude/skills/`。需要 Python 3.10+ 和 Bash；在此文件夹运行 `bash scripts/setup.sh` 一次即可安装依赖。若要使用已有 Python 环境，可安装 `requirements.txt` 并将 `PDF2SCAN_PYTHON` 指向对应解释器。

## Skill 使用示例

在 **Codex** 中输入（默认保留彩色）：

```text
$pdf2scan 请把 /path/to/document.pdf 转成彩色扫描件，保存为 /path/to/document-scan.pdf。
```

在 **Claude Code** 中输入（明确要求黑白）：

```text
/pdf2scan 请把 /path/to/photo.jpg 转成黑白扫描件，保存为 /path/to/photo-scan.pdf。
```

将示例路径换成自己的文件路径。也可以指定一个图片目录，让 skill 按文件名顺序合并输出为一份 PDF。

## 命令行

在此文件夹运行：

```bash
bash scripts/run.sh input.pdf --output scan.pdf
bash scripts/run.sh input.jpg --output scan-bw.pdf --color-mode grayscale
```

复杂照片或打开的双页书刊可能需要手动指定纸张角点；参数格式见 [`references/geometry.md`](references/geometry.md)。输出是图像型 PDF，不能直接选择文字或点击原 PDF 的链接。
