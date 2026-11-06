# NetMind Agent

NetMind Agent 是一个面向自主学习的 AI 工作台。一个问题可以生成多形式讲解、互动教学网页、PPT 课件、思维导图和练习。学习者可引用文档原文提问，按页修订课件、查看和恢复历史版本，并通过理解检查对照回答、原文和判分依据。笔记和学习记录保存在本地。

## 本地运行

需要 Python 3.11+ 和 Node.js 22.12+。

在第一个终端安装并启动后端。

```sh
cd backend
python -m venv .venv
```

激活虚拟环境。Windows PowerShell 使用 `.\.venv\Scripts\Activate.ps1`，macOS 或 Linux 使用 `source .venv/bin/activate`。

```sh
python -m pip install -r requirements.txt
python -m uvicorn main:app --host 127.0.0.1 --port 8000
```

在第二个终端安装并启动前端。

```sh
cd "NetMind Agent"
npm ci
npm run dev -- --host 127.0.0.1
```

打开 [http://127.0.0.1:5173](http://127.0.0.1:5173)。Vite 将 `/api` 转发给本地后端，健康检查地址是 `http://127.0.0.1:8000/api/health`。

使用模型时，将 `backend/.env.example` 复制为 `backend/.env`，填写自己的 `LLM_API_KEY`、`LLM_BASE_URL` 和 `LLM_MODEL`。也可在界面设置里填写。Windows 可在依赖安装完毕后运行 `start_netmind.bat`。

## 知识库与本地数据

全新启动默认使用空白知识库。可以在设置中上传自己的资料，或将有权使用的课件和实验文档放在本地 `base_data/ppt/` 和 `base_data/lab/`，从仓库根目录运行 `python tools/scripts/build_index.py` 重建计算机网络预设。第三方教材及原索引不随源码分发。

应用启动后会在 `backend/data/` 创建数据库。用户设置、学习记录、笔记、上传文档和索引留在该本地目录。请在自己的机器上填写凭据，环境文件和运行数据已加入 Git 忽略规则。

## 测试

前端在 `NetMind Agent/` 下执行 `npm test` 和 `npm run build`。后端在根目录安装 `pytest` 后执行 `python -m pytest tests -q`。测试使用隔离数据库。

- [多形式讲解数据协议](docs/EXPLANATION_CONTRACT.md)
- [第三方组件说明](THIRD_PARTY_NOTICES.md)
