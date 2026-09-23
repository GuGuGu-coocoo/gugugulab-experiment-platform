# 默认字段薄封装示例（R10）

这个目录是一个独立、简短的开发者示例，不修改科学任务：它用真实的独立本地后端
（真实 SQLite，不联网）演示一次声明默认事件类型/ schema / 显式字段或
snapshot_provider，之后用短的 `record()` 记录事件。

从仓库根目录运行：

```sh
godot --headless --path examples/synthetic_experiment --script ../data_defaults_demo/demo.gd
```

可选环境变量：`GEP_SYNTHETIC_STORAGE`（本地存储目录）、`GEP_SYNTHETIC_RESULTS`
（结果目录）。成功时打印一行 `DATA_DEFAULTS_DEMO {...}` 并以 0 退出；结果 JSONL
只在结束时显式保存一次。

示例覆盖：字段列表（逐一显式求值）、snapshot_provider（返回 Dictionary）、
特殊事件的原有四参数显式调用，以及 `record()` / `commit()` / `finish()` 的责任分离；
直接/间接/混合循环与超过 `MAX_VALUE_DEPTH = 64` 的嵌套都会在复制前报 `invalid_value`，
合法共享但无环的子结构照常记录。
接口、错误码与边界见[实验开发者说明](../../docs/experiment_developer.md)。

## 测试专用 Web 入口（P03R10R）

`web_demo_bootstrap.gd` 不是开发者示例的入口，而是浏览器回归用的测试脚本：测试把
`examples/synthetic_experiment` 复制到新的构建目录、只把该文件作为副本的
`bootstrap.gd`，再导出真实 Godot Web 并在真实 Chrome 中通过真实桥与 SDK 走
IndexedDB。它按上面同一套默认字段路径记录两轮结构、执行显式四参数覆盖与复制后
修改源，并确认循环/超深输入不产生任何事件。原项目、科学任务与冻结发行都不因此改变。
