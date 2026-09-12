def build_menu_card(project_name: str, project_dir: str, conv_title: str) -> dict:
    return {
        "config": {"wide_screen_mode": True},
        "header": {"title": {"tag": "plain_text", "content": "🤖 工作台控制面板"}, "template": "blue"},
        "elements": [
            {
                "tag": "div",
                "fields": [
                    {"is_short": True, "text": {"tag": "lark_md", "content": f"**📁 当前工作区:**\n`{project_name}`"}},
                    {"is_short": True, "text": {"tag": "lark_md", "content": f"**💬 当前会话:**\n{conv_title}"}}
                ]
            },
            {"tag": "div", "text": {"tag": "lark_md", "content": f"**📂 本地路径:** `{project_dir}`"}},
            {"tag": "hr"},
            {
                "tag": "div",
                "text": {
                    "tag": "lark_md",
                    "content": (
                        "📋 **快捷操作指引：**\n"
                        "• `/projects` —— 查看与选择工作区项目\n"
                        "• `/project <名称>` —— 切换或新建项目目录\n"
                        "• `/history` —— 查看当前会话历史列表\n"
                        "• `/switch <序号>` —— 切换至指定历史会话\n"
                        "• `/new` —— 开启当前项目的全新会话\n\n"
                        "💡 **直接对话**：发送任意任务指令，AI 即可在本地工作区自动执行并回报！"
                    )
                }
            }
        ]
    }

def build_projects_card(current_project: str, projects: list) -> dict:
    lines = [f"• **👉 📁 {p}** （当前激活）" if p == current_project else f"• 📁 `{p}`" for p in projects]
    return {
        "config": {"wide_screen_mode": True},
        "header": {"title": {"tag": "plain_text", "content": "📂 项目工作区列表"}, "template": "indigo"},
        "elements": [
            {"tag": "div", "text": {"tag": "lark_md", "content": "**现有项目目录：**\n" + "\n".join(lines)}},
            {"tag": "hr"},
            {"tag": "div", "text": {"tag": "lark_md", "content": "👉 发送 `/project <项目名>`（如 `/project 飞书`）即可切换目标目录！"}}
        ]
    }

def build_history_card(current_id: str, history_list: list) -> dict:
    if not history_list:
        content = "暂无历史会话记录。发送 `/new` 可开启新对话。"
    else:
        items = []
        for idx, h in enumerate(history_list, 1):
            cur = " 👉 **[当前会话]**" if h.get("is_current") else ""
            items.append(f"**[{idx}]** 💬 {h['title']} *({h.get('turn_count', 1)} 轮)*{cur}")
        content = "\n".join(items)

    return {
        "config": {"wide_screen_mode": True},
        "header": {"title": {"tag": "plain_text", "content": "📜 历史会话列表"}, "template": "turquoise"},
        "elements": [
            {"tag": "div", "text": {"tag": "lark_md", "content": content}},
            {"tag": "hr"},
            {"tag": "div", "text": {"tag": "lark_md", "content": "👉 发送 `/switch <序号>`（例如 `/switch 1`）切回对应会话\n👉 发送 `/new` 开启新会话"}}
        ]
    }
