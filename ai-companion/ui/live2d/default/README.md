# Live2D 默认模型目录（T3-01）

放置 Live2D 模型文件到此目录，前端会自动加载。

## 文件结构

```
ui/live2d/default/
├── default.model3.json    ← 模型定义（入口文件）
├── default.moc3           ← 网格数据
├── default.png            ← 纹理
├── motions/               ← 动作文件
│   ├── idle_01.motion3.json
│   ├── tap_body_01.motion3.json
│   └── ...
├── expressions/           ← 表情文件（T3-02 用）
│   ├── happy.exp3.json
│   ├── sad.exp3.json
│   ├── angry.exp3.json
│   ├── neutral.exp3.json
│   └── excited.exp3.json
└── physics3.json          ← 物理模拟（头发摆动等）
```

## 获取模型

1. **免费模型**：从以下来源下载免费 Live2D 模型
   - Live2D 官方样例：https://www.live2d.com/download/sample-data/
   - Live2D Cubism Sample：https://www.live2d.com/en/learn/sample/
   - Booth（部分免费）：https://booth.pm/

2. **自制模型**：用 Live2D Cubism Editor 制作
   - 下载：https://www.live2d.com/en/sdk/download/
   - 教程：https://www.live2d.com/en/learn/tutorials/

3. **商业模型**：购买后需遵守授权协议

## 依赖安装

```bash
cd ui/
npm install
```

依赖：
- `pixi.js@6.5.10` — PIXI.js 渲染引擎
- `pixi-live2d-display@0.4.0` — Live2D Cubism Web SDK 封装
- `live2dcubismcore` — Live2D Cubism Core SDK（需手动放置到 `public/live2d/core/live2dcubismcore.min.js`）

## Live2D Cubism Core SDK

pixi-live2d-display 需要 Live2D Cubism Core SDK 运行库：
1. 从 https://www.live2d.com/en/sdk/download/web/ 下载 Web SDK
2. 把 `Core/live2dcubismcore.min.js` 放到 `ui/public/live2d/core/`
3. 在 `index.html` 中引入 `<script src="live2d/core/live2dcubismcore.min.js"></script>`

## 无模型时

前端会自动显示 CSS 占位形象（简笔画头像 + 眨眼动画），不影响对话功能。
