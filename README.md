# SOP计划看板

本地运行的月度 SOP 计划看板。网页和 Excel 共用同一份计划数据，支持滚动 M+6、锁单对照、版本维护、导入导出与 Excel 快照备份。

## 启动

1. 安装 Python 3.10+，运行 `python -m pip install -r requirements.txt`。
2. 同级存在 `../共享数据/` 时，从 `../共享数据/订单汇总/` 读取订单分析生成的销量文件；独立使用且没有共享目录时仍读取 `input_file/`。点击“网页维护”即可在主表改车型总量、展开改版本，或增删行，再点击“保存修改”；无需选择维护月份或维护粒度。Excel保留导入、导出能力，`../共享数据/SOP计划/SOP计划维护.xlsx` 是可选的同步文件（没有共享根时使用 `input_file/`）；没有它时，启动、网页维护和导出都不会自动创建。需要长期表格同步时，到“数据与规则”创建维护Excel。
3. 双击 `启动工作台.cmd`，服务就绪后自动打开 <http://127.0.0.1:8876/>，启动窗口保持运行。
4. 关闭该命令窗口或按 `Ctrl+C` 即停止工作台，无需独立停止脚本。仅关闭浏览器页面不会停止服务。

重复双击会打开已运行的网页，服务仍由第一次启动的窗口控制。旧版隐藏后台进程会在新入口首次启动时核对工作目录、进程身份及端口后切换；其他服务不受影响。命令行也可运行 `python server.py --open-browser`；`./start-sop.ps1 -NoBrowser` 适合不自动打开网页的前台运行。

启动脚本优先使用 `SOP_PYTHON_PATH` 指定的 Python，其次使用本机 Codex 运行环境，最后使用 PATH 中的 Python。Excel 生成脚本优先使用 `SOP_NODE_PATH` 指定的 Node，其次使用本机 Codex 运行环境，最后使用 PATH 中的 Node。

**Excel 运行依赖：** 安装 `requirements.txt` 后，维护表、网页导出和快照备份都可由 Python 独立生成，适用于没有 Codex 运行环境的内网机器。若本机已有 `@oai/artifact-tool`，程序仍可使用 Node 生成；该包缺失或 Node 无法启动时自动转用 Python。设置 `SOP_EXCEL_ENGINE=python` 可直接使用 Python 生成。`scripts/node_modules` 是可自动重建的本机链接，不提交到 Git。

**共享代际规则：** 将“SOP看板”和“订单分析”放在同级目录，保留 `../订单分析/scripts/config/shared_config.py` 的 `MERGE_GROUPS` 字典。看板每次读取此相对路径，不硬编码车型映射；规则修改后刷新即生效。页面可切换单代际与汇总代际，汇总计划自动相加、只读，未参与汇总的车型仍可维护。规则缺失或无效时会明确报错，不能同时展示两层数量。该外部配置由“订单分析”项目维护，不复制进本仓库。

**输入路径：** `--source-dir`（数据导入命令）或网关 `source_dir` 显式配置优先；默认读取顺序为 `SOP_SOURCE_DIR`、`SHARED_DATA_ROOT/订单汇总`、同级 `../共享数据/订单汇总`，最后仅在同级共享根不存在时兼容 `input_file/`。`SOP_SOURCE_DIR` 和 `SHARED_DATA_ROOT` 环境变量请填写绝对路径。显式指定的共享根或已存在的共享根缺少订单文件时会报告缺失，不改读旧副本。订单汇总目录只提供只读输入；计划维护表默认位于共享根的 `SOP计划/SOP计划维护.xlsx`，数据库和规则仍由原项目维护。其他汇总报表不会加入 SOP 销量计算。

**计划表路径：** 网关 `plan_file` 或命令行 `--plan-file` 显式路径沿用既有优先级，其次为 `SOP_PLAN_DIR/SOP计划维护.xlsx`、`SHARED_DATA_ROOT/SOP计划/SOP计划维护.xlsx`、同级 `../共享数据/SOP计划/SOP计划维护.xlsx`；没有共享根时兼容 `input_file/SOP计划维护.xlsx`。`SOP_PLAN_DIR` 请填写绝对路径。创建与同步维护表写入所选计划目录，销售源目录不参与此选择。门户预览仍将计划表复制到隔离目录，并通过网关 `plan_file` 绑定该副本。

## 目录

| 路径 | 用途 | 提交到 Git |
| --- | --- | --- |
| `server.py`、`sop_*.py` | 服务、数据、计划及 Excel 同步逻辑 | 是 |
| `web/` | 页面和图标 | 是 |
| `scripts/` | Excel 生成与数据检查脚本 | 是，`node_modules` 除外 |
| `tests/` | 自动化测试 | 是，生成文件除外 |
| `docs/` | [业务规则与使用说明](docs/业务规则与使用说明.md) | 是 |
| `../共享数据/订单汇总/` | 只读销售来源；与其他项目共用 | 否 |
| `../共享数据/SOP计划/` | 可同步读写的 `SOP计划维护.xlsx` | 否 |
| `input_file/` | 无共享根的独立项目兼容输入目录 | 否 |
| `data/` | 数据库、来源解析、设置、快照及日志 | 否 |
| `output_file/` | 临时导出和本地核验文件 | 否 |

源代码仍位于项目根目录，保持现有 Python 导入和启动方式；运行数据与临时文件通过 `.gitignore` 排除。换电脑时，复制 `data/`、`input_file/` 及所使用的共享输入和计划目录，才能保留当前业务状态。不要仅依靠 Git 仓库保存计划或历史快照。

## 验证

```powershell
python -m unittest discover -s tests -v
```

本机已安装 Microsoft Excel 时，可用原生只读打开检查生成文件。此检查使用正常打开方式，遇到需要修复的文件会失败，并核对标题合并、列宽及隐藏列：

```powershell
./tests/verify_excel_native.ps1 -Paths ./tests/artifacts/inline-layout/export.xlsx
```

详细的业务口径、表格维护、导入导出和快照规则见[业务规则与使用说明](docs/业务规则与使用说明.md)。


## 单一源码与门户接入

本仓库是 SOP 唯一业务源码，原有独立启动方式保留。门户以前缀代理方式接入，或消费本仓库构建的发布产物。身份、只读与数据路径配置参见 [门户接入说明](PORTAL-INTEGRATION.md)。
