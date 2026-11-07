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


def format_size(bytes_num: int) -> str:
    """Formats bytes into human readable string (B, KB, MB, GB)."""
    if not bytes_num or bytes_num < 0:
        return "0 KB"
    if bytes_num < 1024:
        return f"{bytes_num} B"
    elif bytes_num < 1024 * 1024:
        return f"{round(bytes_num / 1024, 1)} KB"
    elif bytes_num < 1024 * 1024 * 1024:
        return f"{round(bytes_num / (1024 * 1024), 1)} MB"
    else:
        return f"{round(bytes_num / (1024 * 1024 * 1024), 2)} GB"


def format_progress_bar(current: int, total: int, width: int = 10) -> str:
    """Generates ASCII/block progress bar [████░░░░░░] 40.0%."""
    if total <= 0:
        return ""
    ratio = min(1.0, max(0.0, current / total))
    filled = int(round(ratio * width))
    bar = "█" * filled + "░" * (width - filled)
    percent = round(ratio * 100, 1)
    return f"`[{bar}] {percent}%`"


def build_upload_progress_card(
    file_name: str,
    status_stage: str,  # "downloading", "extracting", "rendering", "complete", "error"
    current_bytes: int = 0,
    total_bytes: int = 0,
    current_idx: int = 1,
    total_files: int = 1,
    detail_msg: str = "",
    case_materials: list = None,
    is_legal_bot: bool = True
) -> dict:
    """
    Builds dynamic interactive cards for real-time file upload progress and completion status.
    Supports real-time patch updates during streaming download, extraction, and scanned PDF page rendering.
    """
    multi_file_prefix = f"（第 {current_idx}/{total_files} 份）" if total_files > 1 else ""
    size_str = format_size(total_bytes or current_bytes)

    if status_stage == "downloading":
        bar_str = format_progress_bar(current_bytes, total_bytes)
        progress_info = f"{bar_str} `({format_size(current_bytes)} / {size_str})`" if bar_str else f"`已传输 {format_size(current_bytes)}`"
        elements = [
            {
                "tag": "div",
                "text": {
                    "tag": "lark_md",
                    "content": (
                        f"**📁 正在接收材料:** 《{file_name}》 {multi_file_prefix}\n"
                        f"**⏬ 传输进度:** {progress_info}\n\n"
                        f"⏳ 正在从飞书云端通道高速下载至办案工作区，请稍候..."
                    )
                }
            }
        ]
        return {
            "config": {"wide_screen_mode": True},
            "header": {"title": {"tag": "plain_text", "content": "📥 案卷材料接收中..."}, "template": "blue"},
            "elements": elements
        }

    elif status_stage == "extracting":
        step_text = detail_msg or "正在解压案卷归档包并预处理文档内容..."
        elements = [
            {
                "tag": "div",
                "text": {
                    "tag": "lark_md",
                    "content": (
                        f"**📁 目标材料:** 《{file_name}》 {multi_file_prefix}（{size_str}）\n"
                        f"**⚙️ 当前步骤:** {step_text}\n\n"
                        f"⏳ 大文件/归档包解压预处理中，请稍候..."
                    )
                }
            }
        ]
        return {
            "config": {"wide_screen_mode": True},
            "header": {"title": {"tag": "plain_text", "content": "⚙️ 案卷解压与文档预解析中..."}, "template": "orange"},
            "elements": elements
        }

    elif status_stage == "rendering":
        step_text = detail_msg or "正在将扫描件 PDF 逐页高清渲染与图文识别..."
        elements = [
            {
                "tag": "div",
                "text": {
                    "tag": "lark_md",
                    "content": (
                        f"**📁 处理材料:** 《{file_name}》 {multi_file_prefix}（{size_str}）\n"
                        f"**📄 案卷解析:** {step_text}\n\n"
                        f"⏳ 扫描件高清原件渲染与高精 OCR 文字识别中，识别后将直接汇入本案材料库供 AI 秒级起草文书..."
                    )
                }
            }
        ]
        return {
            "config": {"wide_screen_mode": True},
            "header": {"title": {"tag": "plain_text", "content": "⚙️ 案卷渲染与图文识别中..."}, "template": "orange"},
            "elements": elements
        }

    elif status_stage == "complete":
        elements = []
        detail_line = f"\n{detail_msg}" if detail_msg else ""
        header_text = (
            f"**📁 接收材料:** 《{file_name}》 {multi_file_prefix}（{size_str}）"
            f"{detail_line}"
        )
        elements.append({"tag": "div", "text": {"tag": "lark_md", "content": header_text}})

        # Material Checklist
        if case_materials:
            elements.append({"tag": "hr"})
            lines = []
            for i, item in enumerate(case_materials, 1):
                sz = item.get("size_kb", 0)
                sz_str = f"{round(sz / 1024, 1)} MB" if sz >= 1024 else f"{sz} KB"
                char_str = f"（纯文本 {item['char_count']} 字）" if item.get("char_count") else ""
                page_str = f"（高清原件 {len(item.get('rendered_pages', []))} 页）" if item.get("rendered_pages") else ""
                lines.append(f"**[{i}]** 《{item['name']}》 `({sz_str})` {char_str}{page_str}")
            checklist_text = f"📋 **【当前本案在案材料清单】（共 {len(case_materials)} 份）：**\n" + "\n".join(lines)
            elements.append({"tag": "div", "text": {"tag": "lark_md", "content": checklist_text}})

        elements.append({"tag": "hr"})
        if is_legal_bot:
            guide_text = (
                "💡 **快捷办案指引：**\n"
                "本案材料已就绪！如还有补充证据请继续发送；若材料已齐备，请直接发送具体指令，或点击下方快捷词复制发送：\n\n"
                "• **【综合案情研判】** —— 梳理本案事实脉络、争议焦点穿透及诉讼质证策略\n"
                "• **【起草法律意见书】** —— 出具甘肃乾祐律师事务所实务标准格式法律意见书\n"
                "• **【起草民事答辩状】** —— 针对原告诉讼请求及证据出具诉讼答辩状\n"
                "• **【起草代理词】** —— 出具乾祐标准一审/二审/再审民商事实务代理词"
            )
        else:
            guide_text = "💡 如还有补充材料请继续发送；若材料已齐备，请直接发送指令开始处理。"
        elements.append({"tag": "div", "text": {"tag": "lark_md", "content": guide_text}})

        return {
            "config": {"wide_screen_mode": True},
            "header": {"title": {"tag": "plain_text", "content": "✅ 案卷材料归集就绪"}, "template": "turquoise"},
            "elements": elements
        }

    else:  # error
        elements = [
            {
                "tag": "div",
                "text": {
                    "tag": "lark_md",
                    "content": (
                        f"**📁 目标材料:** 《{file_name}》 {multi_file_prefix}\n"
                        f"**❌ 异常说明:** {detail_msg or '接收或处理过程中发生异常。'}\n\n"
                        f"💡 请尝试重新上传该文件，或检查网络连接。"
                    )
                }
            }
        ]
        return {
            "config": {"wide_screen_mode": True},
            "header": {"title": {"tag": "plain_text", "content": "⚠️ 材料接收或解析异常"}, "template": "red"},
            "elements": elements
        }


def format_duration(seconds: int) -> str:
    """Formats seconds into human readable duration string."""
    if seconds < 60:
        return f"{seconds} 秒"
    minutes = seconds // 60
    rem = seconds % 60
    return f"{minutes} 分 {rem} 秒" if rem > 0 else f"{minutes} 分钟"


def build_ai_progress_card(
    task_title: str,
    status_stage: str,  # "running", "complete", "error"
    elapsed_seconds: int = 0,
    step_desc: str = "",
    progress_pct: int = 0,
    is_legal_bot: bool = True
) -> dict:
    """
    Builds dynamic real-time progress cards during AI reasoning, reading, OCR, and document drafting.
    """
    dur_str = format_duration(elapsed_seconds)
    bar_str = format_progress_bar(progress_pct, 100)

    if status_stage == "running":
        header_title = "⚖️【乾祐AI实务研判】办案推进中..." if is_legal_bot else "🤖 AI 任务正在执行中..."
        step_text = step_desc or ("正在全案通读卷宗并梳理事实脉络..." if is_legal_bot else "正在分析与执行任务...")
        elements = [
            {
                "tag": "div",
                "text": {
                    "tag": "lark_md",
                    "content": (
                        f"**📋 研判事项:** {task_title}\n"
                        f"**⏱️ 研判耗时:** `{dur_str}`  |  **⚡ 进度概览:** {bar_str}\n\n"
                        f"**🔍 正在推进:**\n"
                        f"> {step_text}\n\n"
                        f"⏳ 复杂案卷正进行全真图文穿透与要件对齐，系统每数秒自动刷新动态，请稍候..."
                    )
                }
            }
        ]
        return {
            "config": {"wide_screen_mode": True},
            "header": {"title": {"tag": "plain_text", "content": header_title}, "template": "blue"},
            "elements": elements
        }

    elif status_stage == "complete":
        header_title = "✅【乾祐AI实务研判】文书出具完成" if is_legal_bot else "✅ AI 任务执行完成"
        elements = [
            {
                "tag": "div",
                "text": {
                    "tag": "lark_md",
                    "content": (
                        f"**📋 研判事项:** {task_title}\n"
                        f"**⏱️ 研判总耗时:** `{dur_str}`  |  **⚡ 进度:** `[██████████] 100.0%`\n\n"
                        f"🎉 **全案事实梳理、争议焦点穿透及文书正文已生成完毕，请查阅下方完整文书：**"
                    )
                }
            }
        ]
        return {
            "config": {"wide_screen_mode": True},
            "header": {"title": {"tag": "plain_text", "content": header_title}, "template": "turquoise"},
            "elements": elements
        }

    else:  # error
        header_title = "⚠️ 研判任务执行异常"
        elements = [
            {
                "tag": "div",
                "text": {
                    "tag": "lark_md",
                    "content": (
                        f"**📋 研判事项:** {task_title}\n"
                        f"**⏱️ 耗时:** `{dur_str}`\n\n"
                        f"**❌ 异常说明:** {step_desc or '执行过程中遇到未知错误。'}\n\n"
                        f"💡 请尝试重新发送指令或联系管理员排查。"
                    )
                }
            }
        ]
        return {
            "config": {"wide_screen_mode": True},
            "header": {"title": {"tag": "plain_text", "content": header_title}, "template": "red"},
            "elements": elements
        }


