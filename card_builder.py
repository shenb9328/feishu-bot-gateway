def build_menu_card(project_name: str, project_dir: str, conv_title: str, channel_name: str = None) -> dict:
    fields = [
        {"is_short": True, "text": {"tag": "lark_md", "content": f"**📁 绑定项目:**\n`{project_name}`"}},
        {"is_short": True, "text": {"tag": "lark_md", "content": f"**💬 当前会话:**\n{conv_title}"}}
    ]
    if channel_name:
        fields.insert(0, {"is_short": True, "text": {"tag": "lark_md", "content": f"**🏢 所属频道:**\n{channel_name}"}})

    return {
        "config": {"wide_screen_mode": True},
        "header": {"title": {"tag": "plain_text", "content": "🤖 工作台控制面板"}, "template": "blue"},
        "elements": [
            {
                "tag": "div",
                "fields": fields
            },
            {"tag": "div", "text": {"tag": "lark_md", "content": f"**📂 工作区目录:** `{project_dir}`"}},
            {"tag": "hr"},
            {
                "tag": "div",
                "text": {
                    "tag": "lark_md",
                    "content": (
                        "📋 **话题群与项目协作指引：**\n"
                        "• **新建话题即开启新会话**：在群内直接发送新指令发起新话题，AI 自动在对应项目目录下执行！\n"
                        "• **话题内连续跟进**：点击左侧话题列表，即可在右侧继承该话题的全部上下文！\n"
                        "• `/project <名称>` —— 更改本频道或当前会话绑定的本地项目目录\n"
                        "• `/projects` —— 查看本机所有项目目录及频道绑定状态\n"
                        "• `/status` —— 查看当前频道与话题会话的详细状态\n"
                        "• `/new` —— 在当前话题内重置记忆开启新上下文"
                    )
                }
            }
        ]
    }

def build_projects_card(current_project: str, projects: list, channel_name: str = None) -> dict:
    lines = [f"• **👉 📁 {p}** （当前激活）" if p == current_project else f"• 📁 `{p}`" for p in projects]
    ch_info = f"**🏢 当前频道：** `{channel_name}`\n" if channel_name else ""
    return {
        "config": {"wide_screen_mode": True},
        "header": {"title": {"tag": "plain_text", "content": "📂 项目工作区列表"}, "template": "indigo"},
        "elements": [
            {"tag": "div", "text": {"tag": "lark_md", "content": f"{ch_info}**本机可用项目工作区：**\n" + "\n".join(lines)}},
            {"tag": "hr"},
            {"tag": "div", "text": {"tag": "lark_md", "content": "👉 发送 `/project <项目名>`（如 `/project 飞书`）即可为本频道重新绑定项目目录！"}}
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
