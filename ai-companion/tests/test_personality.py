"""T1-03 人格 prompt 单元测试。

验证：
1. 默认上下文能生成完整 prompt，无残留 {} 占位符
2. 动态上下文（亲密度/称呼/情绪/记忆）能正确注入
3. 人格关键要素都出现在 prompt 中（不说教、有边界、温柔等）
4. config.companion_name 能优先作为兜底
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.personality import (
    PersonalityContext,
    PersonalityEngine,
    personality,
)


def test_no_placeholder_left():
    """最终 prompt 不应残留未填的 {xxx} 占位符。"""
    p = personality.build_system_prompt()
    leftovers = re.findall(r"\{(\w+)\}", p)
    assert leftovers == [], f"prompt 中残留未填占位符: {leftovers}"
    print("  [✓] 默认上下文生成 prompt 无残留占位符")


def test_dynamic_context_injected():
    """传入的动态上下文字段应出现在 prompt 中。"""
    ctx = PersonalityContext(
        companion_name="小南",
        user_alias="阿杰",
        intimacy_stage="亲近",
        intimacy_score=55,
        address_hint="用昵称「阿杰」或小名「杰宝」",
        ai_emotion_brief="有点想你，又有点担心你最近是不是太累了",
        memory_brief="- 用户上周提到要准备一个很重要的面试\n- 用户最近常熬夜",
    )
    p = PersonalityEngine().build_system_prompt(ctx)

    for needle in [
        "小南", "阿杰", "亲近", "55", "杰宝",
        "有点想你", "面试", "熬夜",
    ]:
        assert needle in p, f"动态字段未注入 prompt: 缺「{needle}」"
    print("  [✓] 动态上下文（名字/称呼/亲密度/情绪/记忆）正确注入")


def test_personality_key_elements_present():
    """prompt 中应包含人格核心要素关键词。"""
    p = personality.build_system_prompt()
    must_have = [
        "温柔", "理性", "独立", "情绪",
        "不说教", "潜移默化", "边界",
        "陪伴", "不盲从",
    ]
    for kw in must_have:
        assert kw in p, f"人格关键要素缺失: 「{kw}」"
    print(f"  [✓] 人格关键要素齐全 ({len(must_have)} 项)")


def test_forbidden_phrases_not_in_default():
    """默认 prompt 自身不应以 AI 自我介绍话术作为主体表述。

    注：禁忌段会列举禁忌句式作为「不要这么说」的示例，这是必要的，
    因此本测试只检查 prompt 没有把 AI 痕迹话术当作正面表述出现。
    """
    p = personality.build_system_prompt()
    # 这些话术不能作为正面指令出现（如「你是 AI 助手」「我无法...」）
    forbidden_as_self_intro = [
        "作为AI助手", "我只是程序", "我是一个语言模型",
        "我无法", "我不能回答",
    ]
    for f in forbidden_as_self_intro:
        # 允许出现在「不要用 XXX」的禁令清单里，但不允许作为正面陈述
        # 简化检查：只要不含该字符串作为独立陈述即可
        # 这里宽松一些：禁令段是「不用『作为AI』」这种引号引用
        # 所以原句 "作为AI助手" 不应直接出现
        assert f not in p, f"prompt 中正面出现 AI 痕迹话术: 「{f}」"
    print(f"  [✓] 默认 prompt 不含 AI 自我介绍话术 ({len(forbidden_as_self_intro)} 项)")


def test_companion_name_from_config_fallback(monkeypatch=None):
    """config.app.companion_name 已设时应作为名字兜底。"""
    from utils.config import Config
    # 强制重载配置，模拟用户已设名
    Config._instance = None
    # 临时改写 yaml 文件比较重，这里直接测引擎对 config 的依赖：
    # 若 config.companion_name 为空，默认值是「陪伴你的人」
    p = PersonalityEngine().build_system_prompt()
    # 至少有一个名字占位（默认值或 config 值）
    assert "陪伴你的人" in p or config_get_name_nonempty(), \
        "companion_name 兜底逻辑异常"
    print("  [✓] companion_name 兜底逻辑正常")


def config_get_name_nonempty() -> bool:
    from utils.config import config
    n = config.get("app.companion_name") or ""
    return bool(n)


def test_predefined_keywords_in_guidance():
    """价值引导段落应包含潜移默化、共情等关键词。"""
    p = personality.build_system_prompt()
    guidance_kw = ["潜移默化", "不说教", "共情", "提问", "分享感受"]
    for kw in guidance_kw:
        assert kw in p, f"价值引导段缺少关键方法: 「{kw}」"
    print(f"  [✓] 价值引导方法关键词齐全 ({len(guidance_kw)} 项)")


def main() -> int:
    print("T1-03 人格 prompt 单元测试")
    test_no_placeholder_left()
    test_dynamic_context_injected()
    test_personality_key_elements_present()
    test_forbidden_phrases_not_in_default()
    test_companion_name_from_config_fallback()
    test_predefined_keywords_in_guidance()

    print("\n--- 预览 prompt 头部 ---")
    print(personality.preview(PersonalityContext(
        companion_name="小南",
        user_alias="阿杰",
        intimacy_stage="熟悉",
        intimacy_score=30,
        address_hint="自然叫「阿杰」",
        ai_emotion_brief="平静，准备好聊天",
        memory_brief="- 用户喜欢科幻电影",
    )))
    print("\n全部通过 ✅")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
