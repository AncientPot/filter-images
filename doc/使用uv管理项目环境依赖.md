一律使用uv进行环境依赖管理，首先要确保处于虚拟环境中，以下为参考指令：

**1. 初始化项目(创建 pyproject.toml)**

```powershell
uv init
```

**2. 添加依赖(会同步修改 pyproject.toml)**

```powershell
uv add requests
```

前往设置的对应索引源添加依赖

```powershell
uv add torch torchvision torchaudio --index https://download.pytorch.org/whl/cu126
```

**3. 删除依赖(会同步修改 pyproject.toml)**

```powershell
uv remove requests
```

- remove删除后，会同步删除多余的间接依赖

**4. 为项目依赖创建或更新锁文件**

```powershell
uv lock
```

**5. 检查锁定文件与依赖是否一致**

```powershell
uv lock --check
```

**6. 根据锁文件和pyproject.toml 安装或更新依赖**

```powershell
uv sync
```

**7. 检查虚拟环境是否与锁定文件同步**

```powershell
uv sync --check
```

**8. 清理缓存**

```powershell
uv cache clean
```

**9. 运行 Python 文件**

```powershell
uv run app.py
```

> 禁止使用uv install，因为它不会更新pyproject.toml