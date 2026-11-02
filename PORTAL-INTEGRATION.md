# SOP 单一源码与门户接入

本仓库是 SOP 的唯一业务源码。已核对的开发机路径为 `D:\05 AI脚本\PythonProject\计划看板`，合并前基线为 `eec3b69`。门户仓库 `D:\05 AI脚本\统一门户` 仅维护前端入口、接入配置和部署工具，不再维护 `services/sop` 业务源码或回合并补丁。

## 独立运行

原有 `启动工作台.cmd`、`start-sop.ps1` 和 `python server.py --port 8876` 继续用于本机独立运行。不传 `--gateway-config` 时仍使用本机 Host/Origin 校验、原有业务目录和维护人流程。

## 门户模式

`server.py --gateway-config <配置文件>` 显式启用前缀、同源嵌入和可信代理模式。配置包含 `base_path`、`public_origin`、`proxy_secret_env`、`allow_writes`，以及可选的 `data_dir`、`source_dir`、`shared_config`、`plan_file` 绝对路径。

后端仅接受回环代理、有效随机 key 和可信用户；写入默认关闭，POST 还校验 Origin。前端根据会话与写入探测决定只读状态；服务端覆盖客户端自报的维护人，导入同样记录可信操作人。IIS Windows 身份桥接源码位于 `integration/iis/SopIdentityBridge.cs`，由当前项目的发布产物提供，门户不保留第二份身份模块源码。

门户本地预览直接从本仓库启动 Python，只将业务数据在线备份到忽略的临时目录。原数据库、原始 Excel 和维护表不会被预览写入。独立模式和代理模式共用同一套业务实现。

## 发布产物

运行 `python scripts/build_release.py --destination <全新绝对目录>` 构建发布目录。`sop-release.json` 记录源码提交、工作区状态和每个发布文件的 SHA256。发布内容不包含业务数据库、Excel、真实共享规则、机密、Git 元数据或测试输出。

门户打包只接受通过 manifest 校验的 SOP 发布目录，部署包中的 `services/sop` 是只读发布产物布局，不是需要在门户维护的第二份源码。更新 SOP 应修改本仓库、验证并重新构建产物。

## 验证与运维边界

Python 业务/HTTP/导入导出测试与 Node 前端测试在本仓库运行。门户中的相关测试只验证接入、启动参数、发布文件完整性和代理行为。测试使用隔离数据。

内网域名、Windows 服务账号、写入域组、HTTPS 与 IIS/ARR 仍需在目标服务器由管理员配置并验收；源码合并与构建不会自动修改这些设置。
