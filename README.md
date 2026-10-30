# 智卷 — 智能错题本生成系统

基于大模型视觉理解 + LangChain Agent 的智能错题本生成系统。上传试卷 PDF 或图片，自动识别文档结构、智能分割题目、识别学生作答与红笔批改，导出为 Markdown 错题本并存入错题库。支持 AI 解题与教学讲解、笔记整理、独立 AI 对话、错题复习计划与数据统计。

## 界面预览

| 首页 | 错题工作台 |
|------|------------|
| ![登录过程](assets/readme/登录过程.gif) | ![错题工作台](assets/readme/错题工作台.png) |

| 笔记整理 | 主题切换 |
|----------|----------|
| ![笔记分割过程](assets/readme/笔记分割过程.gif) | ![主题切换](assets/readme/主题切换.gif) |

## 功能演示

| AI 找题 | Provider 配置 |
|---------|---------------|
| ![AI 找题](assets/readme/AI找题.gif) | ![Provider 配置](assets/readme/Provider配置.gif) |

## 功能

- **智能分割**：LLM Agent 自动识别题目边界，支持批次并行处理，跨页去重
- **OCR 纠错**：LLM 对识别结果进行结构化纠错，还原原题格式
- **视觉识别**：直接调用支持图像理解的 LLM，一次完成题目、手写作答与红笔批改标记的识别（原 PaddleOCR / 擦除模型保留为后端回退，前端默认不再使用）
- **识别预览**：按页展示原图与题目、黑/蓝笔作答、红笔批改等识别结果，可逐项核对
- **错题库**：SQLite 持久化存储，支持按科目、题型、知识点筛选和统计
- **笔记整理**：上传手写笔记图片，OCR + LLM 自动结构化整理为 Markdown
- **AI 解题**：独立解题 Agent，逐步给出解题过程
- **AI 教学**：教学讲解 Agent，绑定错题上下文的一对一辅导
- **AI 对话**：独立多轮对话，支持 SSE 流式输出和深度思考（DeepSeek Reasoner）；会话对外使用 UUID public_id，避免暴露数据库自增主键，并支持 `/app/ai-chat/:sessionId` 直达历史会话
- **复习计划**：间隔复习，追踪错题掌握状态
- **数据统计**：Dashboard 可视化，按科目/题型/时间维度分析
- **导出**：导出为 Markdown 文件，按大题分组

## 自 clone 以来的优化

相对 clone 时的原项目，本项目主要做了以下改造：

1. **识别方案：从本地 OCR + 去手写模型改为直接调用 LLM 的识图能力**
   原方案用 PaddleOCR 识别文字、EnsExam 擦除手写，复杂手写、连笔与不规则版面下准确率偏低，擦除还容易破坏题干和插图。现直接调用支持图像理解的厂商模型，一次性完成版面理解、题目切分、学生手写（红/黑笔）与批改标记识别，显著提升复杂试卷的识别准确率与可用性；PaddleOCR 仅保留为后端回退，手写擦除从前端流程中下线。
2. **移除和隐藏大部分空壳功能**
   下线已废弃的 PaddleOCR 前端配置入口、Provider 测试与手写擦除流程，隐藏未完成的占位页面和菜单项，只保留真正可用的功能，减少误导性操作和无效路径。
3. **修改点数计算逻辑**
   免费体验额度从「按次 / 按页」改为按真实模型消耗计费：监听模型调用的 token 用量并折算费用（区分缓存命中与峰谷价），按 1 元 = 10 点换算，不足 1 点按 1 点扣减；额度、个人用量与管理员全局统计统一以「点」为单位展示，特殊账号不受额度限制。
4. **支持 AI 生成解析并识别题目配图**
   新增标准答案与解析生成能力，可对整理后的题目调用解题 Agent 输出标准答案和完整解析；识别链路保留题目配图引用，使题面、配图、答案与解析能够一起展示和导出。
5. **优化 LaTeX 与 Markdown 展示**
   题目、答案与解析统一由富文本容器渲染：Markdown 与 LaTeX（MathJax）自动排版，HTML 内容经白名单净化后展示，修正公式、上下标、多行解析在题目卡片、详情弹窗与导出中的显示效果。

## 项目结构

```
├── backend/                         # Flask 后端（纯 API 服务）
│   ├── core/                        # 核心模块
│   │   ├── config.py                # 集中配置（路径、Settings）
│   │   ├── llm.py                   # LLM 初始化（多 provider 支持）
│   │   ├── state.py                 # 全局会话状态（session_files、锁）
│   │   └── mail.py                  # SMTP 邮件发送
│   ├── web_app.py                   # Flask 应用工厂 + Blueprint 注册
│   ├── routes/                      # Flask Blueprint 路由（auth、chat、device、notes、projects 等）
│   ├── src/                         # 核心模块（LangGraph workflow、OCR 客户端、工具函数）
│   ├── agents/                      # LangChain Agent
│   │   ├── error_correction/        # 题目分割 + OCR 纠错
│   │   ├── solve/                   # 解题 Agent
│   │   ├── teach/                   # 教学讲解 Agent
│   │   └── note/                    # 笔记整理 Agent
│   ├── db/                          # SQLite + SQLAlchemy ORM
│   │   ├── models.py               # 数据模型（User、Question、Note、Chat 等）
│   │   ├── crud/                    # CRUD 模块化包
│   │   └── migrate.py              # 数据库自动迁移
│   └── tests/                       # 后端测试
├── frontend/                        # Vue 3 + Vite + Tailwind CSS
│   ├── app.html                     # SPA 入口
│   └── src/
│       ├── views/                   # 页面级组件（HomeView、WorkspaceView）
│       ├── components/              # 基础组件与业务组件
│       ├── composables/             # 组合式函数（useAuth、useTheme 等）
│       ├── router/                  # Vue Router 路由配置
│       ├── api/                     # 按领域拆分的 API 调用层
│       ├── utils/                   # 工具函数（Markdown 渲染、MathJax、DOMPurify）
│       └── __tests__/               # 前端测试（Vitest）
├── example_uploads/                 # 示例测试文件
├── rules/                           # 项目规则、协作流程和开发规范
├── backend/.env.example             # 环境变量模板
└── requirements.txt                 # Python 依赖
```

## 环境部署

### 1. 安装依赖

需要 Python 3.11+、Node.js 18+。

```bash
# 后端依赖
pip install -r requirements.txt

# 前端依赖
cd frontend && npm install
```

### 2. 配置环境变量

```bash
# 复制模板到项目根目录
cp backend/.env.example .env
```

编辑 `.env`，必须配置 `SECRET_KEY`。

LLM API Provider（OpenAI / Anthropic）配置已迁移到数据库，启动后在系统设置页面管理。

SMTP 邮件配置（注册验证码、找回密码）通过 `.env` 的 `APP_SMTP_*` 变量管理，详见 `backend/.env.example`。

### 3. 启动

**开发模式**（前后端分离，支持热更新）：

```bash
# 终端 1：启动后端
cd backend && python web_app.py

# 终端 2：启动前端开发服务器
cd frontend && npm run dev
```

前端开发服务器会自动将 `/api`、`/images`、`/download`、`/erased`、`/uploads` 请求代理到后端 `localhost:5001`。

访问 **http://localhost:5173** 即可使用。

> **注意**：后端已重构为纯 API 服务器，不提供前端页面。直接访问 `:5001` 只会得到 JSON 404。

更完整的启动说明见 [rules/workflow/开发启动规则.md](rules/workflow/开发启动规则.md)，测试与构建说明见 [rules/workflow/测试构建规则.md](rules/workflow/测试构建规则.md)。

## 支持的文件格式

PDF(`.pdf`)、图片(`.jpg` `.jpeg` `.png` `.bmp` `.tiff` `.webp`)，单次上传限制 50 MB。

## 测试

```bash
# 后端测试
cd backend && python -m pytest tests/ -v

# 前端测试
cd frontend && npm test
```

详见 [backend/tests/README.md](backend/tests/README.md) 和 [frontend/src/__tests__/README.md](frontend/src/__tests__/README.md)。

## 项目规则

项目规则集中维护在 [AGENTS.md](AGENTS.md)。

- `rules/development/`：架构、前端、后端、测试和代码注释规则
- `rules/workflow/`：提交、协作、同步、启动、环境和测试构建规则

团队协作采用 fork-based workflow：开发者先 fork 主仓库，将分支推送到自己的 fork 仓库，再通过 Pull Request 提交到主仓库 review。

## 相关文档

开发 Agent、工作流、流式输出或 LangGraph 编排相关功能时，优先查阅官方文档：

- [LangChain Python 文档](https://docs.langchain.com/oss/python/langchain/overview)
- [LangGraph Python 文档](https://docs.langchain.com/oss/python/langgraph/overview)
- [LangChain / LangGraph API Reference](https://reference.langchain.com/python/)
- [LangChain 文档索引 llms.txt](https://docs.langchain.com/llms.txt)

## 许可证

本项目基于 [AGPL-3.0](LICENSE) 开源。任何修改或基于本项目的衍生作品（包括网络服务部署）均须以相同许可证开源。
