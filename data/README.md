# 离线 IP 数据库

本目录用于存放 [ip2region](https://github.com/lionsoul2014/ip2region) 的 `.xdb` 数据文件。
数据文件体积较大，**不提交到 Git**（见 `.gitignore`），需要手动下载和更新。

## 文件

| 文件 | 大小（字节） | 说明 |
| --- | --- | --- |
| `ip2region_v4.xdb` | 11,122,642 | IPv4 归属地，必需 |
| `ip2region_v6.xdb` | 37,277,813 | IPv6 归属地，可选 |

当前开发环境使用的 IPv4 库构建于 **2026-10-14**，
SHA-256：`27ecba8c9a6d5e3b280aedbe7ee15c65372b179fbd0dad3845d8c608f2fcace1`。

## 下载地址

```text
https://raw.githubusercontent.com/lionsoul2014/ip2region/master/data/ip2region_v4.xdb
https://raw.githubusercontent.com/lionsoul2014/ip2region/master/data/ip2region_v6.xdb
```

IPv4 在国内访问 GitHub 较慢时，可使用 jsDelivr 镜像（IPv6 文件超过其单文件限制，不能用）：

```text
https://cdn.jsdelivr.net/gh/lionsoul2014/ip2region@master/data/ip2region_v4.xdb
```

## 手动更新步骤

1. 下载到临时文件，例如 `ip2region_v4.xdb.download`；
2. 核对文件大小；
3. 计算 SHA-256：

   ```powershell
   Get-FileHash .\ip2region_v4.xdb.download -Algorithm SHA256
   ```

   ```bash
   sha256sum ip2region_v4.xdb.download
   ```

4. 与后台「独立 IP」页面底部显示的 SHA-256 比对；不同则说明有新数据；
5. 替换本目录下的 `.xdb` 文件，然后重启服务。

应用会把整个文件读入内存，替换磁盘文件后**必须重启服务才会生效**。

## 其他文件

- `country_names_zh.json`：ISO 3166 两字母国家/地区代码 → 中文名称，
  由 CLDR 中文数据生成，用于显示国外 IP 的国家名。此文件需要提交到 Git。

## 许可证

ip2region 采用 Apache-2.0 / MIT 双许可证。数据来源和更新说明见
<https://github.com/lionsoul2014/ip2region/tree/master/data>。
