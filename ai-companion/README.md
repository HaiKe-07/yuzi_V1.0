# AI 陪伴助手

基于需求文档 V1.0 的桌面 AI 陪伴助手项目，第一阶段（核心对话闭环）。

## 目录结构

```
ai-companion/
├── main.py              # 程序入口
├── config.yaml          # 全局配置
├── requirements.txt
├── utils/               # 通用工具
│   ├── config.py        # 配置加载
│   └── logger.py        # 日志系统
├── core/                # 核心服务层
│   ├── conversation.py  # 对话管理（T1-06）
│   ├── personality.py   # 人格引擎（T1-03）
│   ├── memory/          # 记忆系统
│   ├── emotion/         # 情绪系统
│   └── intimacy.py      # 亲密度系统
├── speech/              # 语音 ASR / TTS
├── llm/                 # 大模型适配器
├── ui/                  # 桌面界面
├── tools/               # 工具模块
├── data/                # 本地数据（运行时生成）
└── logs/                # 日志（运行时生成）
```

## 快速开始

1. 创建虚拟环境并安装依赖：
   ```bash
   python -m venv .venv
   # Windows: .venv\Scripts\activate
   source .venv/bin/activate
   pip install -r requirements.txt
   ```

2. 配置密钥（任选其一）：
   - 在项目根创建 `.env` 文件填入各 `${VAR}` 对应值；或
   - 直接在 shell 中 `export DEEPSEEK_API_KEY=...`

3. 启动自检：
   ```bash
   python main.py
   ```

## 当前状态

| 任务 | 状态 |
|------|------|
| T1-01 项目结构搭建 | ✅ 完成 |
| T1-02 LLM 适配器 | ⏳ 下一步 |
| T1-03 人格 Prompt | ⏳ |
| T1-04 ASR | ⏳ |
| T1-05 TTS | ⏳ |
| T1-06 对话管理 | ⏳ |
| T1-07 对话历史 | ⏳ |
| T1-08 桌面窗口 | ⏳ |

## 配置说明

`config.yaml` 中的 `${VAR}` 形式会被环境变量覆盖：
- `DEEPSEEK_API_KEY` — DeepSeek API 密钥
- `XFYUN_APP_ID / XFYUN_API_KEY / XFYUN_API_SECRET` — 讯飞 ASR
- `VOLC_APP_ID / VOLC_ACCESS_TOKEN` — 火山引擎 TTS
- `QWEATHER_API_KEY` — 和风天气

未设置时保留为空字符串，对应模块在调用时自行报错或回退。
