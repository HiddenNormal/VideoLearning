#!/usr/bin/env bash
# =============================================================================
#  WSL 开发环境自检脚本
#  用法：  bash check-wsl-env.sh            （在 WSL 里直接跑，不需要 sudo）
#  作用：  输出 CPU/内存/磁盘/GPU/ffmpeg/Python/依赖/网络 的现状，
#         把整段输出贴回给我，我据此定稿资源清单和实施顺序。
#  注意：  只读检查，不安装、不修改任何东西。
# =============================================================================
set -u

line() { printf '\n\033[1m== %s ==\033[0m\n' "$1"; }
ok()   { printf '  [OK]   %s\n' "$1"; }
bad()  { printf '  [MISS] %s\n' "$1"; }

line "1. WSL / 内核"
if grep -qi microsoft /proc/version; then ok "运行在 WSL 中"; else bad "看起来不是 WSL"; fi
echo "  内核: $(uname -r)"
if [ -f /etc/os-release ]; then . /etc/os-release; echo "  发行版: ${PRETTY_NAME}"; fi
echo "  用户: $(id -un)   主机名: $(hostname)"

line "2. CPU"
echo "  逻辑核数: $(nproc)"
grep -m1 'model name' /proc/cpuinfo | sed 's/^/  /'

line "3. 内存与 swap（WSL2 默认 = 宿主机内存的一半，可用 .wslconfig 调整）"
free -h | sed 's/^/  /'

line "4. 磁盘"
df -h / /mnt/c /mnt/e 2>/dev/null | sed 's/^/  /'
echo "  -- 提示：数据放 WSL 的 ext4（如 ~/data），不要放 /mnt/e（跨文件系统慢 5~10 倍）"

line "5. GPU（WSL 里能直接看到宿主 NVIDIA 卡才算打通）"
if command -v nvidia-smi >/dev/null 2>&1; then
  nvidia-smi --query-gpu=name,memory.total,memory.used,driver_version --format=csv,noheader | sed 's/^/  /'
  ok "nvidia-smi 可用（PyTorch 装 cu12x 轮子即可，不需要在 WSL 里装驱动）"
else
  bad "未检测到 nvidia-smi —— GPU 未透传，需检查 Windows 驱动版本"
fi
if [ -e /usr/lib/wsl/lib/nvidia-smi ]; then ok "/usr/lib/wsl/lib 挂载正常"; fi

line "6. 命令行工具"
for c in ffmpeg ffprobe python3 pip3 git curl wget unzip; do
  if command -v "$c" >/dev/null 2>&1; then
    v=$( { "$c" --version 2>/dev/null || "$c" -version 2>/dev/null; } | head -1 )
    ok "$c  ->  ${v:-版本未知}"
  else
    bad "$c 未安装"
  fi
done
# ffmpeg 需要的两项关键能力
if command -v ffmpeg >/dev/null 2>&1; then
  printf '  ffmpeg 编码器: '
  ffmpeg -hide_banner -encoders 2>/dev/null | grep -qE 'libx264' && printf 'libx264 ' || printf 'libx264(缺) '
  ffmpeg -hide_banner -filters 2>/dev/null | grep -qE 'palettegen' && printf 'palettegen(present)' || printf 'palettegen(缺)'
  echo
fi

line "7. Python 与关键包"
if command -v python3 >/dev/null 2>&1; then
  echo "  $(python3 -V 2>&1)   路径: $(command -v python3)"
  python3 - <<'PY' 2>/dev/null || bad "Python 包检查脚本执行失败（可能是 python3 版本过低）"
import importlib
want = [
    ("torch",         "PyTorch"),
    ("torchvision",   "PyTorch 视觉"),
    ("transformers",  "HF 模型"),
    ("scenedetect",   "镜头切分"),
    ("cv2",           "OpenCV"),
    ("lancedb",       "向量库"),
    ("gradio",        "Web UI"),
    ("PIL",           "图像处理"),
    ("numpy",         "数值"),
    ("faster_whisper","语音转写"),
]
for mod, label in want:
    try:
        m = importlib.import_module(mod)
        print(f"  [OK]   {mod:14s} {getattr(m, '__version__', '?'):<12s} {label}")
    except Exception:
        print(f"  [MISS] {mod:14s} {'':<12s} {label}")
try:
    import torch
    print(f"  torch.cuda.is_available() = {torch.cuda.is_available()}")
    if torch.cuda.is_available():
        print(f"  torch 识别到: {torch.cuda.get_device_name(0)}")
        print(f"  可用显存: {torch.cuda.get_device_properties(0).total_memory/1024**3:.1f} GB")
except Exception:
    pass
PY
else
  bad "python3 未安装"
fi

line "8. 网络可达性（模型/依赖下载源）"
check_url() {
  if curl -sI -m 8 "$1" >/dev/null 2>&1; then ok "$2  $1"; else bad "$2  $1"; fi
}
check_url https://pypi.org/simple/                    "pypi        "
check_url https://pypi.tuna.tsinghua.edu.cn/simple/    "清华镜像    "
check_url https://huggingface.co                      "huggingface "
check_url https://hf-mirror.com                       "HF 国内镜像 "
check_url https://www.modelscope.cn                   "modelscope  "
check_url https://github.com                          "github      "

line "9. 已有相关数据？"
for d in "$HOME/data" "$HOME/videos" /mnt/e /mnt/d; do
  [ -d "$d" ] && { n=$(find "$d" -maxdepth 3 -type f \( -iname '*.mp4' -o -iname '*.mkv' -o -iname '*.avi' -o -iname '*.rmvb' -o -iname '*.flv' \) 2>/dev/null | head -5); [ -n "$n" ] && { echo "  在 $d 找到视频："; echo "$n" | sed 's/^/    /'; }; }
done
echo "  （没列出来也不代表没有，片源位置你直接告诉我就行）"

line "完成"
echo "  把以上全部输出复制粘给我即可。"
echo "  如果某段报错，也照样贴，错误信息本身就有用。"
