# R1/R2 独立代码审查

日期：2026-09-14。范围：本地R1正常Session/结果与Evidence重开、R2真实子进程写后退出/租约缓存恢复。远程Case、生产并发、持久Session续跑均不在本次通过结论内。

只读审查发现1项Important：R1重开只检查status=completed，可能漏掉final、evidence_refs、tool_calls或attempts丢失。修复为逐个比对原始持久结果的全部字段，允许HTTP视图额外增加messages；原字段缺失或值变化均拒绝。新增5项测试覆盖4种字段缺失及完整保留。先见缺失函数红灯，再实现并运行累计相关71项全部通过（17.70秒），见pytest-focused-review.txt。

审查未发现其他Critical/Important问题。该审查不是远程调用或生产验收；最终全量结果见pytest-full-review.txt。后续提交只包含已验证代码、非敏感报告和文档，不含数据库、向量文件、私有模型调用正文或凭证。

## 真实Case后的补充审查

真实Case暴露参数manifest与JSON工具别名问题，修复后再次对已声明别名映射、Incident/allowlist枚举、异常HTTP记账做只读review，未发现新的Critical/Important问题。未知工具不自动映射，scope每次重验，HTTP异常计数后重新抛出异常，未吞掉失败。最终真实R1/R2及773项回归结果见REAL-RESULTS.md；最初静态review与本地替身不能替代这次真实provider验收。
