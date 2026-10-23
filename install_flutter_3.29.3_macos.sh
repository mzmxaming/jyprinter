#!/bin/bash
# ============================================================
# Flutter 降级安装脚本：3.44.8 -> 3.29.3
# 适用场景：macOS 11 Big Sur（Dart VM 3.44.8 要求 macOS 14+ 无法启动）
# 使用方法：在 Mac 终端执行  bash install_flutter_3.29.3_macos.sh
# ============================================================
set -e

FLUTTER_VERSION="3.29.3"
OLD_VERSION="3.44.8"
INSTALL_ROOT="/flutter"
TARGET_DIR="${INSTALL_ROOT}/${FLUTTER_VERSION}"

# ---- 1. 确认当前系统版本 ----
echo "==> 当前系统版本："
sw_vers

# ---- 2. 检测 CPU 架构，选择对应的 Flutter 安装包 ----
ARCH="$(uname -m)"
if [ "$ARCH" = "arm64" ]; then
  PKG="flutter_macos_arm64_${FLUTTER_VERSION}-stable.zip"
else
  PKG="flutter_macos_${FLUTTER_VERSION}-stable.zip"
fi
URL="https://mirrors.tuna.tsinghua.edu.cn/flutter/flutter_infra_release/releases/stable/macos/${PKG}"
echo "==> CPU 架构: ${ARCH}"
echo "==> 下载地址: ${URL}"

# ---- 3. 下载到临时目录（约 1GB，请耐心等待）----
TMP_DIR="$(mktemp -d)"
echo "==> 开始下载..."
curl -fL --retry 3 -o "${TMP_DIR}/flutter.zip" "${URL}"

# ---- 4. 解压并安装到 /flutter/3.29.3 ----
echo "==> 解压安装到 ${TARGET_DIR} ..."
unzip -q "${TMP_DIR}/flutter.zip" -d "${TMP_DIR}"
if [ -d "${TARGET_DIR}" ]; then
  echo "==> 已存在旧目录 ${TARGET_DIR}，先删除"
  sudo rm -rf "${TARGET_DIR}"
fi
sudo mv "${TMP_DIR}/flutter" "${TARGET_DIR}"
sudo chown -R "$(id -u):$(id -g)" "${TARGET_DIR}"
rm -rf "${TMP_DIR}"

# ---- 5. 切换 PATH（zshrc 中旧版本路径替换为新版本）----
if grep -q "/flutter/${OLD_VERSION}/bin" ~/.zshrc 2>/dev/null; then
  sed -i '' "s#/flutter/${OLD_VERSION}/bin#/flutter/${FLUTTER_VERSION}/bin#g" ~/.zshrc
  echo "==> 已将 ~/.zshrc 中的 PATH 从 ${OLD_VERSION} 替换为 ${FLUTTER_VERSION}"
elif ! grep -q "/flutter/${FLUTTER_VERSION}/bin" ~/.zshrc 2>/dev/null; then
  echo "export PATH=\"${TARGET_DIR}/bin:\$PATH\"" >> ~/.zshrc
  echo "==> 已向 ~/.zshrc 添加新 PATH"
fi

# ---- 6. 当前会话立即生效并验证 ----
export PATH="${TARGET_DIR}/bin:$PATH"
echo "==> 验证安装："
flutter --version

echo ""
echo "============================================================"
echo " 安装完成！请执行以下操作："
echo " 1) 关闭并重新打开终端（或执行 source ~/.zshrc）"
echo " 2) 运行 flutter doctor 检查开发环境"
echo " 3) 确认无误后，可手动删除旧版本：sudo rm -rf /flutter/${OLD_VERSION}"
echo "============================================================"
