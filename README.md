# pdf2scan

将 PDF 或页面照片转换为扫描件样式的 PDF。默认保留彩色；按需生成黑白版本。照片输入支持纸张角点检测、透视矫正和阴影提亮。

## 效果展示

下图使用开发阶段的四张原 JPEG；原始照片不随仓库发布。每行对应一张照片，从左到右依次是**原图、pdf2scan 彩色输出、pdf2scan 黑白输出**。

![四组原图与 pdf2scan 彩色、黑白效果对比](pdf2scan/assets/showcase-original-color-grayscale.jpg)

## 获取与使用

克隆本仓库，将其中的 [`pdf2scan/`](pdf2scan/) 目录复制到 Codex 的 `~/.agents/skills/` 或 Claude Code 的 `~/.claude/skills/`。需要 Python 3.10+ 和 Bash；在 skill 目录运行 `bash scripts/setup.sh` 一次即可安装依赖。也可以直接在仓库目录运行：

```bash
git clone https://github.com/Codekiing/pdf2scan.git
cd pdf2scan
bash pdf2scan/scripts/setup.sh
bash pdf2scan/scripts/run.sh input.pdf --output scan.pdf
bash pdf2scan/scripts/run.sh input.jpg --output scan-bw.pdf --color-mode grayscale
```

复杂照片或打开的双页书刊可能需要手动指定纸张角点；参数格式见 [`references/geometry.md`](pdf2scan/references/geometry.md)。输出是图像型 PDF，不能直接选择文字或点击原 PDF 的链接。
