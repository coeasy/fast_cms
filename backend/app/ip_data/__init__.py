"""离线 IP 归属地数据目录（ip2region xdb）。

声明为包是为了让非 editable 安装（wheel）也能带上 ``*.xdb``：
``pyproject.toml`` 的 ``[tool.setuptools.package-data]`` 只对包目录生效，
没有 ``__init__.py`` 时 setuptools 会忽略这里的文件，装完 IP 归属地就静默失效。
"""
