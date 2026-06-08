# AIXiaoMi-Glass

AIXiaoMi 的运行可视化项目。第一版聚焦“朋友圈智能相册”场景：以一个上传批次为锚点，把照片上传、事件投递、预处理、智能判断、额度冻结、相册生成、结果推送、结算扣费、文件清理串成一条可视化流程。

Glass 包含两部分：

- Server：读取 MySQL 业务数据，并尽量采集服务日志。
- Web：用准实时页面展示完整流程，默认每 3 秒刷新一次。

## 本地启动

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt

set MYSQL_HOST=127.0.0.1
set MYSQL_PORT=3306
set MYSQL_USER=root
set MYSQL_PASSWORD=your-password
set CORE_ALBUM_DB=core_album_db
set ACCOUNT_DB=account_db

uvicorn app.main:app --host 0.0.0.0 --port 8010
```

打开：

```text
http://127.0.0.1:8010
```

## 接口

```text
GET /health
GET /api/scenarios/moments-album/flows
GET /api/scenarios/moments-album/flows/{upload_batch_id}
GET /api/scenarios/moments-album/logs
```

## 页面说明

- 左侧：最近的相册流程，可按用户 ID 筛选。
- 中间：九个阶段的实时状态。
- 下方：照片状态、智能判断、生成结果、扣费推送和最近日志。

## 部署建议

推荐部署目录：

```text
/opt/aixiaomi/AIXiaoMi-Glass
```

推荐服务端口：

```text
8010
```

敏感配置通过环境变量或服务器本地 env 文件保存，不提交到仓库。
