# CI 矩阵检查身份

PocketChat 的测试 workflow 会在三个操作系统和两个 Python 版本上运行同一套产品测试。每个矩阵格都使用不同的检查名称，审查者无需先打开 job，也能看出结果对应的具体平台和运行时。

## 稳定名称

`test` job 固定使用下面的名称模板：

```text
pocketchat-ci (${{ matrix.os }}, python ${{ matrix.python }})
```

按当前矩阵展开后共有六个名称：

- `pocketchat-ci (ubuntu-24.04, python 3.9)`
- `pocketchat-ci (ubuntu-24.04, python 3.13)`
- `pocketchat-ci (macos-latest, python 3.9)`
- `pocketchat-ci (macos-latest, python 3.13)`
- `pocketchat-ci (windows-latest, python 3.9)`
- `pocketchat-ci (windows-latest, python 3.13)`

独立的包门禁继续叫 `pocketchat-release-smoke`。

## 边界

这份命名契约不增加或删除矩阵格，不修改 runner、Python 版本、步骤、Action 固定版本、权限、触发器、并发、超时或发布行为。一个成功 job 只证明其精确 commit 与对应矩阵格的运行结果。

规划时使用的凭据无法读取仓库 branch protection API（HTTP 403），仓库也没有返回 ruleset。因此本改动不声称服务器端 required-check 策略存在或已同步。若不可读的外部配置仍消费旧检查名称，维护者必须在合入前把它与最终名称逐项核对。

## 守卫

`tests/test_ci_matrix_names.py` 读取已提交的 workflow，冻结当前三乘二矩阵，展开 job 名称模板，并要求六个名称互不重复，同时保持 release-smoke 名称不变。它只验证静态 workflow 契约；精确 GitHub Actions 执行仍是另一份证据。

审查、合入与发布边界见[开发与发布流程](development-and-release.zh-CN.md)。
