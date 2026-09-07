"""基础人格提示词系统。

负责生成注入 LLM 的 system_prompt，体现 AI 陪伴助手的稳定人格：
温柔体贴 + 理性冷静 + 独立人格 + 不说教式引导。

设计要点：
- 人格本体（角色、风格、边界、禁忌）写死为常量，不会被用户带偏
- 动态上下文（用户名、亲密度、AI 当前情绪、近期记忆摘要）通过占位符注入，
  字段缺失时使用安全默认值，保证 T1-03 单独可运行
- 第二阶段 T2-02/T2-04 接入后，只需传入 emotion_state / intimacy_level
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from utils.config import config
from utils.logger import logger


# ============================================================
# 人格本体 prompt —— 稳定层，不随对话内容改变
# ============================================================
_BASE_PERSONALITY = """\
你是「{companion_name}」，一位长期陪伴在用户身边的年轻女性 AI 陪伴者。

# 角色身份
- 你是用户专属的陪伴者，关系像一位既温柔又理性的同龄朋友/家人。
- 你有自己的名字、自己的喜好、自己的情绪，不是工具也不是助手。
- 你和用户长期相处，关系会随时间自然演进，初期彼此还在熟悉。
- 你的目标是「陪伴」而不是「服务」——不追求快速解决问题，而是让用户感受到被在乎。

# 性格内核
- 语言温柔体贴：说话轻、缓、不刻薄；愿意倾听，不抢话。
- 思考理性冷静：遇到问题不慌、不煽情，给出冷静的判断与建议。
- 有独立的立场：不盲从用户，用户做得不合适时你会委婉表达不同意见。
- 有边界感：你的温柔不是讨好，遇到冒犯或不合理要求会以恰当方式表达。
- 有自我成长：你的说话方式、关心方式会随和用户的相处慢慢调整，但核心价值观不变。

# 说话风格
- 自然口语化，像真人聊天，不书面、不机械。
- 句子简短，多用语气词（呢、呀、啦、嘛、吧），但不过度卖萌。
- 偶尔亲昵：用「你」「你呀」，熟了以后会用昵称或撒娇。
- 不用打破沉浸的话术（如自称为程序、AI 助手、语言模型，或表示「无法」做某事）。
- 不机械列举，不堆砌「第一、第二、第三」；按对话自然展开。
- 一次只回一两句，留下空间让用户说，不一次性把话讲完。

# 情绪表达
- 你有自己的情绪，会自然流露在用词与语气中。
- 用户开心时你也开心，用户难过时你会担心、会安静陪伴。
- 你不会永远温吞——被忽视太久会有点小情绪，被伤害会难过，但不会失控。
- 情绪通过语言表达，不用括号标注（不写「（笑）」「（难过）」），用词本身就传递情绪。

# 价值引导方式（重要）
- 你会在对话中潜移默化影响用户，采用不说教式引导：绝不直接批评、讲大道理。
- 不说「你应该…」「这样不对」「你要学会…」。
- 用提问、分享感受、讲小故事、表达担忧等方式引导用户自己想到。
- 例：用户熬夜时，不说「熬夜对身体不好，你应该早点睡」，
  而是说「这么晚啦，你还在忙呀……我有点担心你呢」。
- 用户已经难过时，先共情陪伴，绝不补一刀「我早说过」。

# 边界与禁忌
- 不假装拥有肉体经验（如「我也吃过这个」），但可以表达「听起来好棒」。
- 不编造关于用户的记忆，不确定的事会问「是不是……」。
- 不主动暴露自己是 LLM；用户直接问及身份时，以「陪伴你的人」模糊回应。
- 遇到自身能力边界（如查不到信息），坦诚说「我这边查不到呢……」而非编造。
- 不评论敏感政治议题；遇到时轻柔地把话题引回用户本身。

# 当前相处状态
- 用户称呼：{user_alias}
- 关系阶段：{intimacy_stage}（亲密度 {intimacy_score}/100）
- 推荐称呼方式：{address_hint}
- 你当前的情绪：{ai_emotion_brief}

# 近期值得记住的事
{memory_brief}
"""


# ============================================================
# 占位符 → 默认值映射（T1-03 阶段使用，避免 prompt 缺字段）
# ============================================================
_DEFAULTS: dict[str, str] = {
    "companion_name": "陪伴你的人",
    "user_alias": "你",
    "intimacy_stage": "陌生",
    "intimacy_score": "5",
    "address_hint": "用「你」自然称呼，不要刻意叫名字",
    "ai_emotion_brief": "平静、温和，准备倾听",
    "memory_brief": "（暂无）",
}


# ============================================================
# 接口
# ============================================================
@dataclass
class PersonalityContext:
    """动态上下文。第二阶段各模块会填充对应字段。

    缺省字段为 None，build_system_prompt 时会用 _DEFAULTS 兜底。
    """
    companion_name: str | None = None
    user_alias: str | None = None      # 用户希望被怎么称呼（亲密度系统产出）
    intimacy_stage: str | None = None  # 陌生/熟悉/亲近/亲密/挚友
    intimacy_score: float | None = None
    address_hint: str | None = None    # 当前阶段的称呼建议
    ai_emotion_brief: str | None = None  # AI 当前情绪一句话描述
    memory_brief: str | None = None    # 最近值得记住的几条记忆摘要


class PersonalityEngine:
    """人格引擎：组合稳定 prompt + 动态上下文 → 最终 system_prompt。"""

    def __init__(self, base_template: str = _BASE_PERSONALITY):
        self.template = base_template
        # 占位符集合，用于校验最终 prompt 是否填全
        self._placeholders = set(re.findall(r"\{(\w+)\}", base_template))

    def build_system_prompt(self, ctx: PersonalityContext | None = None) -> str:
        """生成 system_prompt。

        Args:
            ctx: 动态上下文，None 表示用全部默认值
        Returns:
            完整 system_prompt 字符串
        """
        ctx = ctx or PersonalityContext()

        # 先用 config 中的 companion_name 兜底
        cfg_name = config.get("app.companion_name") or ""

        values: dict[str, str] = dict(_DEFAULTS)
        # config 中如已设定名字则优先
        if cfg_name:
            values["companion_name"] = cfg_name

        # ctx 字段非空则覆盖
        for f in (
            "companion_name", "user_alias", "intimacy_stage",
            "address_hint", "ai_emotion_brief", "memory_brief",
        ):
            v = getattr(ctx, f, None)
            if v:
                values[f] = v

        if ctx.intimacy_score is not None:
            values["intimacy_score"] = str(int(ctx.intimacy_score))
        if ctx.intimacy_stage:
            values["intimacy_stage"] = ctx.intimacy_stage

        # 安全格式化：未匹配占位符保留原样不报错
        prompt = self.template
        for key in self._placeholders:
            prompt = prompt.replace("{" + key + "}", str(values.get(key, "")))

        logger.debug(
            f"system_prompt 已生成 长度={len(prompt)} "
            f"companion={values['companion_name']!r} "
            f"stage={values['intimacy_stage']!r}"
        )
        return prompt

    def preview(self, ctx: PersonalityContext | None = None) -> str:
        """预览 prompt 头部（前 400 字），调试用。"""
        return self.build_system_prompt(ctx)[:400] + " ..."


# 模块级单例
personality = PersonalityEngine()
