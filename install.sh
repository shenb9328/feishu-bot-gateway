#!/usr/bin/env bash
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SKILLS_DIR="$HOME/.gemini/config/skills/feishu-agent"
SYSTEMD_USER_DIR="$HOME/.config/systemd/user"

echo "=========================================================="
echo "🚀 开始安装 Feishu Agent Gateway & 全局 Antigravity 技能"
echo "=========================================================="

# 1. 检查 Python 运行环境
if ! command -v python3 &>/dev/null; then
    echo "❌ 错误: 未检测到 python3，请先安装 Python 3.10+"
    exit 1
fi
echo "✅ Python 3 环境: $(python3 --version)"

# 2. 安装依赖
echo "📦 正在安装 Python 依赖 (lark-oapi)..."
python3 -m pip install -r "$SCRIPT_DIR/requirements.txt" --break-system-packages 2>/dev/null || \
python3 -m pip install -r "$SCRIPT_DIR/requirements.txt" --user

# 3. 配置文件生成
if [ ! -f "$SCRIPT_DIR/config.json" ]; then
    if [ -f "$HOME/.config/feishu/config.json" ]; then
        echo "🔍 检测到系统全局飞书凭据 (~/.config/feishu/config.json)，正在自动同步..."
        APP_ID=$(python3 -c "import json; print(json.load(open('$HOME/.config/feishu/config.json')).get('app_id', ''))")
        APP_SECRET=$(python3 -c "import json; print(json.load(open('$HOME/.config/feishu/config.json')).get('app_secret', ''))")
        cat << CJSON > "$SCRIPT_DIR/config.json"
{
  "app_id": "$APP_ID",
  "app_secret": "$APP_SECRET",
  "projects_root": "$HOME",
  "allowed_open_ids": []
}
CJSON
        echo "✅ 已自动从现有凭据生成 config.json"
    else
        echo "⚠️ 未检测到已存凭据，正在从模板生成 config.json..."
        cp "$SCRIPT_DIR/config.json.example" "$SCRIPT_DIR/config.json"
        echo "👉 请根据提示编辑 $SCRIPT_DIR/config.json 填入你的 App ID 与 App Secret！"
    fi
else
    echo "✅ 已存在配置文件: $SCRIPT_DIR/config.json"
fi

# 4. 注册全局 Antigravity Skill
echo "🧠 正在注册全局 Antigravity Skill..."
mkdir -p "$(dirname "$SKILLS_DIR")"
rm -rf "$SKILLS_DIR"
ln -sf "$SCRIPT_DIR" "$SKILLS_DIR"
echo "✅ 已成功将技能软链接至: $SKILLS_DIR"

# 5. 配置 Systemd 用户守护进程
echo "⚙️ 正在配置 Systemd 用户守护服务..."
mkdir -p "$SYSTEMD_USER_DIR"
cat << SSERVICE > "$SYSTEMD_USER_DIR/feishu-agent.service"
[Unit]
Description=Feishu Agent Gateway (Antigravity Bridge)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=$SCRIPT_DIR
Environment="PATH=$HOME/.gemini/antigravity-cli/bin:$HOME/.local/bin:/usr/local/bin:/usr/bin:/bin"
Environment="PYTHONUNBUFFERED=1"
ExecStart=/usr/bin/python3 -u $SCRIPT_DIR/bot_gateway.py
Restart=always
RestartSec=5
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=default.target
SSERVICE

systemctl --user daemon-reload
systemctl --user enable feishu-agent.service
systemctl --user restart feishu-agent.service

# 尝试启用系统级免登录驻留
if command -v loginctl &>/dev/null; then
    loginctl enable-linger "$USER" 2>/dev/null || true
fi

echo "=========================================================="
echo "🎉 安装与配置全部就绪！"
echo "• 技能路径: $SKILLS_DIR"
echo "• 服务状态: active (running)"
echo "• 查看日志: journalctl --user -u feishu-agent -f"
echo "=========================================================="
