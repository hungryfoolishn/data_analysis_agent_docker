# Git 提交流程备忘

项目仓库：https://github.com/hungryfoolishn/data_analysis_agent

## SSH 配置（已完成）

```bash
# 生成 SSH Key（已生成）
ssh-keygen -t ed25519 -C "data_analysis_agent" -f ~/.ssh/id_ed25519 -N ""

# 公钥已添加到 GitHub Deploy Keys（读写权限）
# ~/.ssh/id_ed25519.pub
```

## 日常提交流程

```bash
# 1. 查看变更
git status
git diff

# 2. 暂存所有变更
git add -A

# 3. 提交
git commit -m "feat: 简要描述本次修改内容"

# 4. 推送到 GitHub
git push origin main
```

## 提交信息规范

| 前缀 | 用途 |
|------|------|
| `feat:` | 新功能 |
| `fix:` | 修复 bug |
| `refactor:` | 重构（不改变功能） |
| `docs:` | 文档更新 |
| `test:` | 测试相关 |
| `chore:` | 构建/配置/依赖 |
| `perf:` | 性能优化 |

## 常用命令

```bash
# 查看提交历史
git log --oneline -10

# 撤销未提交的修改
git checkout -- <file>

# 查看远程仓库
git remote -v

# 强制推送（谨慎使用）
git push origin main --force

# 拉取远程更新
git pull origin main
```
