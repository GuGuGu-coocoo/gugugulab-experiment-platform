# Docker 组件公告逐项复核（2026-10-23）

这是[安全审计](SECURITY_AUDIT.md)的组件附表，只适用于本轮 Linux amd64 候选应用镜像（源码 `6009d7d`），不代表已发布附件或用户现有实例。

以实际镜像的软件清单、OSV 查询和 Debian trixie 发行商记录复核原 139 个公告 ID，并保留续检新增项。Debian 回补版本经容器内 `dpkg --compare-versions` 核对；不根据上游版本号猜测。

“组件残余／未发现应用可达”不表示漏洞消失或严格证明不可达：对应系统组件仍可能含缺陷，本机执行权限、额外扩展或部署配置变化需重新评估。发行商 `unimportant` 不是 GEP 的整体风险等级。未执行恶意归档、内存破坏、提权或压力攻击。

系统命令的判断依据为服务源码：除固定参数的首次初始化子进程外，不把请求输入交给 shell／系统命令；实验包不在服务器执行。当前固定依赖不提供 PAM 登录、FTP、nscd／systemd 服务或任意 SQL／数据库上传接口。

处理分类：候选镜像已含回补 58；发行商判定不受影响 1；发行商未确认／保留未知 1；组件已移除 12；组件残余／未发现应用可达 67。公告 ID 含别名，不能作为独立漏洞数。

| 组件 | 公告 | 实际源包版本 | 本轮判断 | 依据与限制 |
|---|---|---|---|---|
| acl | [CVE-2026-54369](https://security-tracker.debian.org/tracker/CVE-2026-54369) | `2.3.2-2` | 组件残余／未发现应用可达 | 本机 pathname ACL／符号链接竞态；GEP 不执行 ACL 管理，上传 ZIP 不调用 libacl 路径接口。（发行商：open） |
| acl | [CVE-2026-54370](https://security-tracker.debian.org/tracker/CVE-2026-54370) | `2.3.2-2` | 组件残余／未发现应用可达 | 本机 pathname ACL／符号链接竞态；GEP 不执行 ACL 管理，上传 ZIP 不调用 libacl 路径接口。（发行商：open） |
| apt | [CVE-2011-3374](https://security-tracker.debian.org/tracker/CVE-2011-3374) | `3.0.3` | 组件残余／未发现应用可达 | apt-key／系统软件安装路径；GEP 请求不调用包管理器。（发行商：open） |
| attr | [CVE-2026-54371](https://security-tracker.debian.org/tracker/CVE-2026-54371) | `1:2.5.2-3` | 组件残余／未发现应用可达 | getfattr／setfattr 工具；GEP 不运行这些工具处理请求。（发行商：open） |
| bzip2 | [CVE-2026-42250](https://security-tracker.debian.org/tracker/CVE-2026-42250) | `1.0.8-6` | 组件残余／未发现应用可达 | bzip2recover 工具；GEP 上传明确拒绝 BZIP2，未调用恢复工具。（发行商：open） |
| coreutils | [CVE-2017-18018](https://security-tracker.debian.org/tracker/CVE-2017-18018) | `9.7-3` | 组件残余／未发现应用可达 | chown／sort／uniq／unexpand 的特定命令参数或本机竞态；GEP 不以参与者输入执行这些命令。（发行商：open） |
| coreutils | [CVE-2025-5278](https://security-tracker.debian.org/tracker/CVE-2025-5278) | `9.7-3` | 组件残余／未发现应用可达 | chown／sort／uniq／unexpand 的特定命令参数或本机竞态；GEP 不以参与者输入执行这些命令。（发行商：open） |
| coreutils | [CVE-2026-56391](https://security-tracker.debian.org/tracker/CVE-2026-56391) | `9.7-3` | 组件残余／未发现应用可达 | chown／sort／uniq／unexpand 的特定命令参数或本机竞态；GEP 不以参与者输入执行这些命令。（发行商：open） |
| coreutils | [CVE-2026-56392](https://security-tracker.debian.org/tracker/CVE-2026-56392) | `9.7-3` | 组件残余／未发现应用可达 | chown／sort／uniq／unexpand 的特定命令参数或本机竞态；GEP 不以参与者输入执行这些命令。（发行商：open） |
| dash | [CVE-2026-102473](https://security-tracker.debian.org/tracker/CVE-2026-102473) | `0.5.12-12` | 组件残余／未发现应用可达 | shell 匹配／printf 路径；GEP 不把网络输入交给 shell。（发行商：open） |
| dash | [CVE-2026-102474](https://security-tracker.debian.org/tracker/CVE-2026-102474) | `0.5.12-12` | 组件残余／未发现应用可达 | shell 匹配／printf 路径；GEP 不把网络输入交给 shell。（发行商：open） |
| diffutils | [CVE-2026-53910](https://security-tracker.debian.org/tracker/CVE-2026-53910) | `1:3.10-4` | 组件残余／未发现应用可达 | diff3 命令；GEP 不调用 diff3。（发行商：open） |
| gcc-14 | [CVE-2026-102010](https://security-tracker.debian.org/tracker/CVE-2026-102010) | `14.2.0-19` | 组件残余／未发现应用可达 | 特定 C++ erase_if／aligned new 接口；未在 GEP Python／Rust 验证链路发现对应调用，未证明全部扩展的二进制不可达。（发行商：open） |
| gcc-14 | [CVE-2026-95619](https://security-tracker.debian.org/tracker/CVE-2026-95619) | `14.2.0-19` | 组件残余／未发现应用可达 | 特定 C++ erase_if／aligned new 接口；未在 GEP Python／Rust 验证链路发现对应调用，未证明全部扩展的二进制不可达。（发行商：open） |
| glibc | [CVE-2010-4756](https://security-tracker.debian.org/tracker/CVE-2010-4756) | `2.41-12+deb13u4` | 组件残余／未发现应用可达 | 需受控 libc glob 表达式；没有 FTP/glob 查询端点，上传名称不交给 libc glob。（发行商：open） |
| glibc | [CVE-2018-20796](https://security-tracker.debian.org/tracker/CVE-2018-20796) | `2.41-12+deb13u4` | 组件残余／未发现应用可达 | 需 libc 正则；GEP 不调用 libc regexec，Schema 正则已禁用。（发行商：open） |
| glibc | [CVE-2019-1010022](https://security-tracker.debian.org/tracker/CVE-2019-1010022) | `2.41-12+deb13u4` | 组件残余／未发现应用可达 | 需先有栈溢出并利用保护绕过；上游认为非独立安全问题。没有据此排除其他内存破坏。（发行商：open） |
| glibc | [CVE-2019-1010023](https://security-tracker.debian.org/tracker/CVE-2019-1010023) | `2.41-12+deb13u4` | 组件残余／未发现应用可达 | 需本机恶意 ELF／ldd；上游有争议，上传代码不在服务器执行。（发行商：open） |
| glibc | [CVE-2019-1010024](https://security-tracker.debian.org/tracker/CVE-2019-1010024) | `2.41-12+deb13u4` | 组件残余／未发现应用可达 | 线程堆栈／堆地址复用的 ASLR 前提；上游有争议，不等于已证实应用内存泄漏。（发行商：open） |
| glibc | [CVE-2019-1010025](https://security-tracker.debian.org/tracker/CVE-2019-1010025) | `2.41-12+deb13u4` | 组件残余／未发现应用可达 | ASLR 猜测与本机线程前提；上游有争议，不据此宣称 ASLR 不需要防护。（发行商：open） |
| glibc | [CVE-2019-9192](https://security-tracker.debian.org/tracker/CVE-2019-9192) | `2.41-12+deb13u4` | 组件残余／未发现应用可达 | 需 libc 正则，且上游有争议；GEP 不调用 libc regexec。（发行商：open） |
| glibc | [CVE-2026-18374](https://security-tracker.debian.org/tracker/CVE-2026-18374) | `2.41-12+deb13u4` | 组件残余／未发现应用可达 | 需攻击者控制 fopen 的 ,ccs= mode；GEP 不透传文件打开模式。（发行商：open） |
| glibc | [CVE-2026-19499](https://security-tracker.debian.org/tracker/CVE-2026-19499) | `2.41-12+deb13u4` | 组件残余／未发现应用可达 | 需 strfmon/strfmon_l 的特定格式宽度；GEP 不调用货币格式接口。（发行商：open） |
| glibc | [CVE-2026-19542](https://security-tracker.debian.org/tracker/CVE-2026-19542) | `2.41-12+deb13u4` | 组件残余／未发现应用可达 | 需 tdelete 极深树；未发现 GEP 调用，不作完整 C 扩展静态可达证明。（发行商：open） |
| glibc | [CVE-2026-5435](https://security-tracker.debian.org/tracker/CVE-2026-5435) | `2.41-12+deb13u4` | 组件残余／未发现应用可达 | 需已弃用 DNS 记录打印 API；应用仅常规解析，不调用这些调试打印接口。（发行商：open） |
| glibc | [CVE-2026-5450](https://security-tracker.debian.org/tracker/CVE-2026-5450) | `2.41-12+deb13u4` | 候选镜像已含回补 | Debian 修复 2.41-12+deb13u4；实际 dpkg 版本比较满足。（发行商：resolved） |
| glibc | [CVE-2026-5928](https://security-tracker.debian.org/tracker/CVE-2026-5928) | `2.41-12+deb13u4` | 候选镜像已含回补 | Debian 修复 2.41-12+deb13u4；实际 dpkg 版本比较满足。（发行商：resolved） |
| glibc | [CVE-2026-6238](https://security-tracker.debian.org/tracker/CVE-2026-6238) | `2.41-12+deb13u4` | 组件残余／未发现应用可达 | 需已弃用 DNS 记录打印 API，不是通常 DNS resolver 路径。（发行商：open） |
| glibc | [CVE-2026-6368](https://security-tracker.debian.org/tracker/CVE-2026-6368) | `2.41-12+deb13u4` | 组件残余／未发现应用可达 | 需 wordexp WRDE_APPEND；没有 shell／wordexp 输入入口。（发行商：open） |
| glibc | [CVE-2026-6791](https://security-tracker.debian.org/tracker/CVE-2026-6791) | `2.41-12+deb13u4` | 组件残余／未发现应用可达 | 需 wordexp 展开恶意超长 ~用户名；没有对应输入执行路径。（发行商：open） |
| glibc | [CVE-2026-77117](https://security-tracker.debian.org/tracker/CVE-2026-77117) | `2.41-12+deb13u4` | 组件残余／未发现应用可达 | 需 libc iconv SHIFT_JISX0213 转换；API UTF-8/JSON，Python 编码器不直接等同该 libc 调用。（发行商：open） |
| glibc | [CVE-2026-80489](https://security-tracker.debian.org/tracker/CVE-2026-80489) | `2.41-12+deb13u4` | 组件残余／未发现应用可达 | 需 libc iconv EUC_JISX0213 转换；API UTF-8/JSON，未发现对应接口。（发行商：open） |
| glibc | [CVE-2026-8674](https://security-tracker.debian.org/tracker/CVE-2026-8674) | `2.41-12+deb13u4` | 组件残余／未发现应用可达 | 需受控超长 DNS search/LOCALDOMAIN；由部署配置决定，不从请求设置。宿主配置未检查。（发行商：open） |
| glibc | [CVE-2026-86805](https://security-tracker.debian.org/tracker/CVE-2026-86805) | `2.41-12+deb13u4` | 组件残余／未发现应用可达 | 需本机 setuid/setgid、可写路径竞态及特定 hardlink 配置；应用 no-new-privileges、非 root／只读根，未提供本机执行入口。（发行商：open） |
| glibc | [CVE-2026-89092](https://security-tracker.debian.org/tracker/CVE-2026-89092) | `2.41-12+deb13u4` | 组件残余／未发现应用可达 | 需 nscd；候选镜像未安装／运行 nscd，宿主 nscd 不在本结论内。（发行商：open） |
| glibc | [CVE-2026-95818](https://security-tracker.debian.org/tracker/CVE-2026-95818) | `2.41-12+deb13u4` | 组件残余／未发现应用可达 | 需 setuid/setgid 的 $ORIGIN loader 路径；应用不运行上传 ELF，无新权限，宿主另核。（发行商：open） |
| glibc | [CVE-2026-97399](https://security-tracker.debian.org/tracker/CVE-2026-97399) | `2.41-12+deb13u4` | 组件残余／未发现应用可达 | 仅 Power8 优化路径；候选为 amd64，不适用。（发行商：open） |
| gzip | [CVE-2026-41991](https://security-tracker.debian.org/tracker/CVE-2026-41991) | `1.13-1+deb13u1` | 候选镜像已含回补 | Debian 修复 1.13-1+deb13u1；实际 dpkg 版本比较满足。（发行商：resolved） |
| gzip | [CVE-2026-41992](https://security-tracker.debian.org/tracker/CVE-2026-41992) | `1.13-1+deb13u1` | 候选镜像已含回补 | Debian 修复 1.13-1+deb13u1；实际 dpkg 版本比较满足。（发行商：resolved） |
| ncurses | [CVE-2025-6141](https://security-tracker.debian.org/tracker/CVE-2025-6141) | `6.5+20250216-2` | 组件残余／未发现应用可达 | 终端描述／infocmp 工具；服务不开放终端、不解析用户终端定义。（发行商：open） |
| ncurses | [CVE-2025-69720](https://security-tracker.debian.org/tracker/CVE-2025-69720) | `6.5+20250216-2` | 组件残余／未发现应用可达 | 终端描述／infocmp 工具；服务不开放终端、不解析用户终端定义。（发行商：open） |
| openssl | [CVE-2026-14456](https://security-tracker.debian.org/tracker/CVE-2026-14456) | `3.5.7-1~deb13u3` | 候选镜像已含回补 | Debian 修复 3.5.7-1~deb13u2；实际 dpkg 版本比较满足。（发行商：resolved） |
| openssl | [CVE-2026-14457](https://security-tracker.debian.org/tracker/CVE-2026-14457) | `3.5.7-1~deb13u3` | 候选镜像已含回补 | Debian 修复 3.5.7-1~deb13u2；实际 dpkg 版本比较满足。（发行商：resolved） |
| openssl | [CVE-2026-18798](https://security-tracker.debian.org/tracker/CVE-2026-18798) | `3.5.7-1~deb13u3` | 候选镜像已含回补 | Debian 修复 3.5.7-1~deb13u2；实际 dpkg 版本比较满足。（发行商：resolved） |
| openssl | [CVE-2026-35189](https://security-tracker.debian.org/tracker/CVE-2026-35189) | `3.5.7-1~deb13u3` | 候选镜像已含回补 | Debian 修复 3.5.7-1~deb13u3；实际 dpkg 版本比较满足。（发行商：resolved） |
| openssl | [CVE-2026-35191](https://security-tracker.debian.org/tracker/CVE-2026-35191) | `3.5.7-1~deb13u3` | 候选镜像已含回补 | Debian 修复 3.5.7-1~deb13u3；实际 dpkg 版本比较满足。（发行商：resolved） |
| openssl | [CVE-2026-42772](https://security-tracker.debian.org/tracker/CVE-2026-42772) | `3.5.7-1~deb13u3` | 候选镜像已含回补 | Debian 修复 3.5.7-1~deb13u3；实际 dpkg 版本比较满足。（发行商：resolved） |
| openssl | [CVE-2026-54872](https://security-tracker.debian.org/tracker/CVE-2026-54872) | `3.5.7-1~deb13u3` | 候选镜像已含回补 | Debian 修复 3.5.7-1~deb13u3；实际 dpkg 版本比较满足。（发行商：resolved） |
| openssl | [CVE-2026-54873](https://security-tracker.debian.org/tracker/CVE-2026-54873) | `3.5.7-1~deb13u3` | 候选镜像已含回补 | Debian 修复 3.5.7-1~deb13u3；实际 dpkg 版本比较满足。（发行商：resolved） |
| openssl | [CVE-2026-54874](https://security-tracker.debian.org/tracker/CVE-2026-54874) | `3.5.7-1~deb13u3` | 候选镜像已含回补 | Debian 修复 3.5.7-1~deb13u2；实际 dpkg 版本比较满足。（发行商：resolved） |
| openssl | [CVE-2026-54875](https://security-tracker.debian.org/tracker/CVE-2026-54875) | `3.5.7-1~deb13u3` | 候选镜像已含回补 | Debian 修复 3.5.7-1~deb13u3；实际 dpkg 版本比较满足。（发行商：resolved） |
| openssl | [CVE-2026-63072](https://security-tracker.debian.org/tracker/CVE-2026-63072) | `3.5.7-1~deb13u3` | 候选镜像已含回补 | Debian 修复 3.5.7-1~deb13u2；实际 dpkg 版本比较满足。（发行商：resolved） |
| openssl | [CVE-2026-63073](https://security-tracker.debian.org/tracker/CVE-2026-63073) | `3.5.7-1~deb13u3` | 候选镜像已含回补 | Debian 修复 3.5.7-1~deb13u2；实际 dpkg 版本比较满足。（发行商：resolved） |
| openssl | [CVE-2026-63074](https://security-tracker.debian.org/tracker/CVE-2026-63074) | `3.5.7-1~deb13u3` | 候选镜像已含回补 | Debian 修复 3.5.7-1~deb13u2；实际 dpkg 版本比较满足。（发行商：resolved） |
| openssl | [CVE-2026-63075](https://security-tracker.debian.org/tracker/CVE-2026-63075) | `3.5.7-1~deb13u3` | 候选镜像已含回补 | Debian 修复 3.5.7-1~deb13u2；实际 dpkg 版本比较满足。（发行商：resolved） |
| openssl | [CVE-2026-63076](https://security-tracker.debian.org/tracker/CVE-2026-63076) | `3.5.7-1~deb13u3` | 候选镜像已含回补 | Debian 修复 3.5.7-1~deb13u2；实际 dpkg 版本比较满足。（发行商：resolved） |
| openssl | [CVE-2026-72897](https://security-tracker.debian.org/tracker/CVE-2026-72897) | `3.5.7-1~deb13u3` | 候选镜像已含回补 | Debian 修复 3.5.7-1~deb13u3；实际 dpkg 版本比较满足。（发行商：resolved） |
| openssl | [CVE-2026-75803](https://security-tracker.debian.org/tracker/CVE-2026-75803) | `3.5.7-1~deb13u3` | 候选镜像已含回补 | Debian 修复 3.5.7-1~deb13u2；实际 dpkg 版本比较满足。（发行商：resolved） |
| openssl | [CVE-2026-75804](https://security-tracker.debian.org/tracker/CVE-2026-75804) | `3.5.7-1~deb13u3` | 候选镜像已含回补 | Debian 修复 3.5.7-1~deb13u3；实际 dpkg 版本比较满足。（发行商：resolved） |
| openssl | [CVE-2026-75805](https://security-tracker.debian.org/tracker/CVE-2026-75805) | `3.5.7-1~deb13u3` | 候选镜像已含回补 | Debian 修复 3.5.7-1~deb13u3；实际 dpkg 版本比较满足。（发行商：resolved） |
| openssl | [CVE-2026-75806](https://security-tracker.debian.org/tracker/CVE-2026-75806) | `3.5.7-1~deb13u3` | 候选镜像已含回补 | Debian 修复 3.5.7-1~deb13u3；实际 dpkg 版本比较满足。（发行商：resolved） |
| openssl | [CVE-2026-77696](https://security-tracker.debian.org/tracker/CVE-2026-77696) | `3.5.7-1~deb13u3` | 候选镜像已含回补 | Debian 修复 3.5.7-1~deb13u3；实际 dpkg 版本比较满足。（发行商：resolved） |
| openssl | [CVE-2026-84782](https://security-tracker.debian.org/tracker/CVE-2026-84782) | `3.5.7-1~deb13u3` | 候选镜像已含回补 | Debian 修复 3.5.7-1~deb13u3；实际 dpkg 版本比较满足。（发行商：resolved） |
| openssl | [CVE-2026-84784](https://security-tracker.debian.org/tracker/CVE-2026-84784) | `3.5.7-1~deb13u3` | 候选镜像已含回补 | Debian 修复 3.5.7-1~deb13u3；实际 dpkg 版本比较满足。（发行商：resolved） |
| pam | [CVE-2025-8941](https://security-tracker.debian.org/tracker/CVE-2025-8941) | `1.7.0-5` | 发行商未确认／保留未知 | PAM 本机登录模块；GEP 使用 Django 认证，不用 PAM。发行商未裁定项仍保留未知状态。（发行商：undetermined） |
| pam | [CVE-2026-54411](https://security-tracker.debian.org/tracker/CVE-2026-54411) | `1.7.0-5` | 组件残余／未发现应用可达 | PAM 本机登录模块；GEP 使用 Django 认证，不用 PAM。发行商未裁定项仍保留未知状态。（发行商：open） |
| pcre2 | [CVE-2026-103111](https://security-tracker.debian.org/tracker/CVE-2026-103111) | `10.46-1~deb13u3` | 候选镜像已含回补 | Debian 修复 10.46-1~deb13u3；实际 dpkg 版本比较满足。（发行商：resolved） |
| pcre2 | [CVE-2026-86145](https://security-tracker.debian.org/tracker/CVE-2026-86145) | `10.46-1~deb13u3` | 候选镜像已含回补 | Debian 修复 10.46-1~deb13u2；实际 dpkg 版本比较满足。（发行商：resolved） |
| pcre2 | [CVE-2026-89156](https://security-tracker.debian.org/tracker/CVE-2026-89156) | `10.46-1~deb13u3` | 候选镜像已含回补 | Debian 修复 10.46-1~deb13u2；实际 dpkg 版本比较满足。（发行商：resolved） |
| pcre2 | [CVE-2026-89157](https://security-tracker.debian.org/tracker/CVE-2026-89157) | `10.46-1~deb13u3` | 候选镜像已含回补 | Debian 修复 10.46-1~deb13u2；实际 dpkg 版本比较满足。（发行商：resolved） |
| pcre2 | [CVE-2026-89158](https://security-tracker.debian.org/tracker/CVE-2026-89158) | `10.46-1~deb13u3` | 候选镜像已含回补 | Debian 修复 10.46-1~deb13u2；实际 dpkg 版本比较满足。（发行商：resolved） |
| pcre2 | [CVE-2026-89160](https://security-tracker.debian.org/tracker/CVE-2026-89160) | `10.46-1~deb13u3` | 候选镜像已含回补 | Debian 修复 10.46-1~deb13u2；实际 dpkg 版本比较满足。（发行商：resolved） |
| pcre2 | [CVE-2026-89161](https://security-tracker.debian.org/tracker/CVE-2026-89161) | `10.46-1~deb13u3` | 候选镜像已含回补 | Debian 修复 10.46-1~deb13u2；实际 dpkg 版本比较满足。（发行商：resolved） |
| pcre2 | [CVE-2026-89162](https://security-tracker.debian.org/tracker/CVE-2026-89162) | `10.46-1~deb13u3` | 候选镜像已含回补 | Debian 修复 10.46-1~deb13u2；实际 dpkg 版本比较满足。（发行商：resolved） |
| perl | [CVE-2011-4116](https://security-tracker.debian.org/tracker/CVE-2011-4116) | `5.40.1-6+deb13u1` | 组件残余／未发现应用可达 | Perl 的 File::Temp／正则／POD／Archive::Tar；应用请求未启动 Perl，ZIP 校验使用 Python。（发行商：open） |
| perl | [CVE-2025-15649](https://security-tracker.debian.org/tracker/CVE-2025-15649) | `5.40.1-6+deb13u1` | 候选镜像已含回补 | Debian 修复 5.40.1-6+deb13u1；实际 dpkg 版本比较满足。（发行商：resolved） |
| perl | [CVE-2026-12087](https://security-tracker.debian.org/tracker/CVE-2026-12087) | `5.40.1-6+deb13u1` | 候选镜像已含回补 | Debian 修复 5.40.1-6+deb13u1；实际 dpkg 版本比较满足。（发行商：resolved） |
| perl | [CVE-2026-13221](https://security-tracker.debian.org/tracker/CVE-2026-13221) | `5.40.1-6+deb13u1` | 候选镜像已含回补 | Debian 修复 5.40.1-6+deb13u1；实际 dpkg 版本比较满足。（发行商：resolved） |
| perl | [CVE-2026-15534](https://security-tracker.debian.org/tracker/CVE-2026-15534) | `5.40.1-6+deb13u1` | 组件残余／未发现应用可达 | Perl 的 File::Temp／正则／POD／Archive::Tar；应用请求未启动 Perl，ZIP 校验使用 Python。（发行商：open） |
| perl | [CVE-2026-19487](https://security-tracker.debian.org/tracker/CVE-2026-19487) | `5.40.1-6+deb13u1` | 候选镜像已含回补 | Debian 修复 5.40.1-6+deb13u1；实际 dpkg 版本比较满足。（发行商：resolved） |
| perl | [CVE-2026-42496](https://security-tracker.debian.org/tracker/CVE-2026-42496) | `5.40.1-6+deb13u1` | 候选镜像已含回补 | Debian 修复 5.40.1-6+deb13u1；实际 dpkg 版本比较满足。（发行商：resolved） |
| perl | [CVE-2026-42497](https://security-tracker.debian.org/tracker/CVE-2026-42497) | `5.40.1-6+deb13u1` | 候选镜像已含回补 | Debian 修复 5.40.1-6+deb13u1；实际 dpkg 版本比较满足。（发行商：resolved） |
| perl | [CVE-2026-48959](https://security-tracker.debian.org/tracker/CVE-2026-48959) | `5.40.1-6+deb13u1` | 候选镜像已含回补 | Debian 修复 5.40.1-6+deb13u1；实际 dpkg 版本比较满足。（发行商：resolved） |
| perl | [CVE-2026-48961](https://security-tracker.debian.org/tracker/CVE-2026-48961) | `5.40.1-6+deb13u1` | 候选镜像已含回补 | Debian 修复 5.40.1-6+deb13u1；实际 dpkg 版本比较满足。（发行商：resolved） |
| perl | [CVE-2026-48962](https://security-tracker.debian.org/tracker/CVE-2026-48962) | `5.40.1-6+deb13u1` | 候选镜像已含回补 | Debian 修复 5.40.1-6+deb13u1；实际 dpkg 版本比较满足。（发行商：resolved） |
| perl | [CVE-2026-57432](https://security-tracker.debian.org/tracker/CVE-2026-57432) | `5.40.1-6+deb13u1` | 候选镜像已含回补 | Debian 修复 5.40.1-6+deb13u1；实际 dpkg 版本比较满足。（发行商：resolved） |
| perl | [CVE-2026-57433](https://security-tracker.debian.org/tracker/CVE-2026-57433) | `5.40.1-6+deb13u1` | 候选镜像已含回补 | Debian 修复 5.40.1-6+deb13u1；实际 dpkg 版本比较满足。（发行商：resolved） |
| perl | [CVE-2026-7010](https://security-tracker.debian.org/tracker/CVE-2026-7010) | `5.40.1-6+deb13u1` | 候选镜像已含回补 | Debian 修复 5.40.1-6+deb13u1；实际 dpkg 版本比较满足。（发行商：resolved） |
| perl | [CVE-2026-7017](https://security-tracker.debian.org/tracker/CVE-2026-7017) | `5.40.1-6+deb13u1` | 候选镜像已含回补 | Debian 修复 5.40.1-6+deb13u1；实际 dpkg 版本比较满足。（发行商：resolved） |
| perl | [CVE-2026-82560](https://security-tracker.debian.org/tracker/CVE-2026-82560) | `5.40.1-6+deb13u1` | 组件残余／未发现应用可达 | Perl 的 File::Temp／正则／POD／Archive::Tar；应用请求未启动 Perl，ZIP 校验使用 Python。（发行商：open） |
| perl | [CVE-2026-8376](https://security-tracker.debian.org/tracker/CVE-2026-8376) | `5.40.1-6+deb13u1` | 候选镜像已含回补 | Debian 修复 5.40.1-6+deb13u1；实际 dpkg 版本比较满足。（发行商：resolved） |
| perl | [CVE-2026-9538](https://security-tracker.debian.org/tracker/CVE-2026-9538) | `5.40.1-6+deb13u1` | 组件残余／未发现应用可达 | Perl 的 File::Temp／正则／POD／Archive::Tar；应用请求未启动 Perl，ZIP 校验使用 Python。（发行商：open） |
| pip | GHSA-4xh5-x5gv-qwph | `not installed` | 组件已移除 | 构建安装完成后卸载 pip；不把安装器公告当作生产 HTTP 漏洞。多个 ID 可能是同一 CVE 的别名。（发行商：PyPI/OSV） |
| pip | GHSA-58qw-9mgm-455v | `not installed` | 组件已移除 | 构建安装完成后卸载 pip；不把安装器公告当作生产 HTTP 漏洞。多个 ID 可能是同一 CVE 的别名。（发行商：PyPI/OSV） |
| pip | GHSA-6vgw-5pg2-w6jp | `not installed` | 组件已移除 | 构建安装完成后卸载 pip；不把安装器公告当作生产 HTTP 漏洞。多个 ID 可能是同一 CVE 的别名。（发行商：PyPI/OSV） |
| pip | GHSA-jp4c-xjxw-mgf9 | `not installed` | 组件已移除 | 构建安装完成后卸载 pip；不把安装器公告当作生产 HTTP 漏洞。多个 ID 可能是同一 CVE 的别名。（发行商：PyPI/OSV） |
| pip | GHSA-qwm4-qh6w-59xr | `not installed` | 组件已移除 | 构建安装完成后卸载 pip；不把安装器公告当作生产 HTTP 漏洞。多个 ID 可能是同一 CVE 的别名。（发行商：PyPI/OSV） |
| pip | GHSA-wf93-45jw-7689 | `not installed` | 组件已移除 | 构建安装完成后卸载 pip；不把安装器公告当作生产 HTTP 漏洞。多个 ID 可能是同一 CVE 的别名。（发行商：PyPI/OSV） |
| pip | PYSEC-2026-1795 | `not installed` | 组件已移除 | 构建安装完成后卸载 pip；不把安装器公告当作生产 HTTP 漏洞。多个 ID 可能是同一 CVE 的别名。（发行商：PyPI/OSV） |
| pip | PYSEC-2026-1796 | `not installed` | 组件已移除 | 构建安装完成后卸载 pip；不把安装器公告当作生产 HTTP 漏洞。多个 ID 可能是同一 CVE 的别名。（发行商：PyPI/OSV） |
| pip | PYSEC-2026-196 | `not installed` | 组件已移除 | 构建安装完成后卸载 pip；不把安装器公告当作生产 HTTP 漏洞。多个 ID 可能是同一 CVE 的别名。（发行商：PyPI/OSV） |
| pip | PYSEC-2026-2875 | `not installed` | 组件已移除 | 构建安装完成后卸载 pip；不把安装器公告当作生产 HTTP 漏洞。多个 ID 可能是同一 CVE 的别名。（发行商：PyPI/OSV） |
| pip | PYSEC-2026-2876 | `not installed` | 组件已移除 | 构建安装完成后卸载 pip；不把安装器公告当作生产 HTTP 漏洞。多个 ID 可能是同一 CVE 的别名。（发行商：PyPI/OSV） |
| pip | PYSEC-2026-3721 | `not installed` | 组件已移除 | 构建安装完成后卸载 pip；不把安装器公告当作生产 HTTP 漏洞。多个 ID 可能是同一 CVE 的别名。（发行商：PyPI/OSV） |
| shadow | [CVE-2007-5686](https://security-tracker.debian.org/tracker/CVE-2007-5686) | `1:4.17.4-2` | 组件残余／未发现应用可达 | 本机系统账号／subuid／btmp 配置；GEP 不创建系统登录账号，应用用户和 Docker 管理边界分别处理。（发行商：open） |
| shadow | [CVE-2024-56433](https://security-tracker.debian.org/tracker/CVE-2024-56433) | `1:4.17.4-2` | 组件残余／未发现应用可达 | 本机系统账号／subuid／btmp 配置；GEP 不创建系统登录账号，应用用户和 Docker 管理边界分别处理。（发行商：open） |
| sqlite3 | [CVE-2021-45346](https://security-tracker.debian.org/tracker/CVE-2021-45346) | `3.46.1-7+deb13u2` | 组件残余／未发现应用可达 | 需攻击者提供数据库／恶意 SQL；不接受用户数据库文件、只走参数化 ORM。（发行商：open） |
| sqlite3 | [CVE-2025-70873](https://security-tracker.debian.org/tracker/CVE-2025-70873) | `3.46.1-7+deb13u2` | 组件残余／未发现应用可达 | 需 SQLite zipfile 扩展；GEP 归档使用 Python zipfile，不加载该扩展。（发行商：open） |
| sqlite3 | [CVE-2026-11822](https://security-tracker.debian.org/tracker/CVE-2026-11822) | `3.46.1-7+deb13u2` | 候选镜像已含回补 | Debian 修复 3.46.1-7+deb13u2；实际 dpkg 版本比较满足。（发行商：resolved） |
| sqlite3 | [CVE-2026-11824](https://security-tracker.debian.org/tracker/CVE-2026-11824) | `3.46.1-7+deb13u2` | 候选镜像已含回补 | Debian 修复 3.46.1-7+deb13u2；实际 dpkg 版本比较满足。（发行商：resolved） |
| sqlite3 | [CVE-2026-39113](https://security-tracker.debian.org/tracker/CVE-2026-39113) | `3.46.1-7+deb13u2` | 发行商判定不受影响 | Debian trixie fixed_version=0；不是仅比较上游版本。（发行商：resolved） |
| sqlite3 | [CVE-2026-50812](https://security-tracker.debian.org/tracker/CVE-2026-50812) | `3.46.1-7+deb13u2` | 组件残余／未发现应用可达 | 需 SQLite Session changeset 扩展；GEP Session 是应用模型，不调用 sqlite3changeset_apply_v3。（发行商：open） |
| sqlite3 | [CVE-2026-50813](https://security-tracker.debian.org/tracker/CVE-2026-50813) | `3.46.1-7+deb13u2` | 组件残余／未发现应用可达 | 需 SQLite Session changeset concat/merge；未开放这些接口。（发行商：open） |
| systemd | [CVE-2013-4392](https://security-tracker.debian.org/tracker/CVE-2013-4392) | `257.13-1~deb13u1` | 组件残余／未发现应用可达 | 镜像只含 systemd 来源库，不运行 systemd／journald／homed／oomd 服务；宿主同名服务不在此结论内。（发行商：open） |
| systemd | [CVE-2023-31437](https://security-tracker.debian.org/tracker/CVE-2023-31437) | `257.13-1~deb13u1` | 组件残余／未发现应用可达 | 镜像只含 systemd 来源库，不运行 systemd／journald／homed／oomd 服务；宿主同名服务不在此结论内。（发行商：open） |
| systemd | [CVE-2023-31438](https://security-tracker.debian.org/tracker/CVE-2023-31438) | `257.13-1~deb13u1` | 组件残余／未发现应用可达 | 镜像只含 systemd 来源库，不运行 systemd／journald／homed／oomd 服务；宿主同名服务不在此结论内。（发行商：open） |
| systemd | [CVE-2023-31439](https://security-tracker.debian.org/tracker/CVE-2023-31439) | `257.13-1~deb13u1` | 组件残余／未发现应用可达 | 镜像只含 systemd 来源库，不运行 systemd／journald／homed／oomd 服务；宿主同名服务不在此结论内。（发行商：open） |
| systemd | [CVE-2026-15059](https://security-tracker.debian.org/tracker/CVE-2026-15059) | `257.13-1~deb13u1` | 组件残余／未发现应用可达 | 镜像只含 systemd 来源库，不运行 systemd／journald／homed／oomd 服务；宿主同名服务不在此结论内。（发行商：open） |
| systemd | [CVE-2026-16742](https://security-tracker.debian.org/tracker/CVE-2026-16742) | `257.13-1~deb13u1` | 组件残余／未发现应用可达 | 镜像只含 systemd 来源库，不运行 systemd／journald／homed／oomd 服务；宿主同名服务不在此结论内。（发行商：open） |
| systemd | [CVE-2026-40228](https://security-tracker.debian.org/tracker/CVE-2026-40228) | `257.13-1~deb13u1` | 组件残余／未发现应用可达 | 镜像只含 systemd 来源库，不运行 systemd／journald／homed／oomd 服务；宿主同名服务不在此结论内。（发行商：open） |
| tar | [CVE-2005-2541](https://security-tracker.debian.org/tracker/CVE-2005-2541) | `1.35+dfsg-3.1` | 组件残余／未发现应用可达 | GNU tar 的特定抽取／恢复条件；GEP 上传用 zipfile，不调用 GNU tar。备份恢复必须只处理受信归档，不能用本轮合成归档豁免恶意备份风险。（发行商：open） |
| tar | [CVE-2026-18477](https://security-tracker.debian.org/tracker/CVE-2026-18477) | `1.35+dfsg-3.1` | 组件残余／未发现应用可达 | GNU tar 的特定抽取／恢复条件；GEP 上传用 zipfile，不调用 GNU tar。备份恢复必须只处理受信归档，不能用本轮合成归档豁免恶意备份风险。（发行商：open） |
| tar | [CVE-2026-18508](https://security-tracker.debian.org/tracker/CVE-2026-18508) | `1.35+dfsg-3.1` | 组件残余／未发现应用可达 | GNU tar 的特定抽取／恢复条件；GEP 上传用 zipfile，不调用 GNU tar。备份恢复必须只处理受信归档，不能用本轮合成归档豁免恶意备份风险。（发行商：open） |
| tar | [CVE-2026-5704](https://security-tracker.debian.org/tracker/CVE-2026-5704) | `1.35+dfsg-3.1` | 组件残余／未发现应用可达 | GNU tar 的特定抽取／恢复条件；GEP 上传用 zipfile，不调用 GNU tar。备份恢复必须只处理受信归档，不能用本轮合成归档豁免恶意备份风险。（发行商：open） |
| util-linux | [CVE-2022-0563](https://security-tracker.debian.org/tracker/CVE-2022-0563) | `2.41.5-0+deb13u1` | 组件残余／未发现应用可达 | 本机 login／chfn／chsh／mount／nsenter 路径；GEP 不调用这些命令，无新权限且应用移除全部 Linux capabilities。（发行商：open） |
| util-linux | [CVE-2025-14104](https://security-tracker.debian.org/tracker/CVE-2025-14104) | `2.41.5-0+deb13u1` | 候选镜像已含回补 | Debian 修复 2.41.3-1；实际 dpkg 版本比较满足。（发行商：resolved） |
| util-linux | [CVE-2026-13595](https://security-tracker.debian.org/tracker/CVE-2026-13595) | `2.41.5-0+deb13u1` | 候选镜像已含回补 | Debian 修复 2.41.5-0+deb13u1；实际 dpkg 版本比较满足。（发行商：resolved） |
| util-linux | [CVE-2026-27456](https://security-tracker.debian.org/tracker/CVE-2026-27456) | `2.41.5-0+deb13u1` | 候选镜像已含回补 | Debian 修复 2.41.5-0+deb13u1；实际 dpkg 版本比较满足。（发行商：resolved） |
| util-linux | [CVE-2026-3184](https://security-tracker.debian.org/tracker/CVE-2026-3184) | `2.41.5-0+deb13u1` | 组件残余／未发现应用可达 | 本机 login／chfn／chsh／mount／nsenter 路径；GEP 不调用这些命令，无新权限且应用移除全部 Linux capabilities。（发行商：open） |
| util-linux | [CVE-2026-53612](https://security-tracker.debian.org/tracker/CVE-2026-53612) | `2.41.5-0+deb13u1` | 候选镜像已含回补 | Debian 修复 2.41.5-0+deb13u1；实际 dpkg 版本比较满足。（发行商：resolved） |
| util-linux | [CVE-2026-53613](https://security-tracker.debian.org/tracker/CVE-2026-53613) | `2.41.5-0+deb13u1` | 候选镜像已含回补 | Debian 修复 2.41.5-0+deb13u1；实际 dpkg 版本比较满足。（发行商：resolved） |
| util-linux | [CVE-2026-53614](https://security-tracker.debian.org/tracker/CVE-2026-53614) | `2.41.5-0+deb13u1` | 候选镜像已含回补 | Debian 修复 2.41.5-0+deb13u1；实际 dpkg 版本比较满足。（发行商：resolved） |
| util-linux | [CVE-2026-53615](https://security-tracker.debian.org/tracker/CVE-2026-53615) | `2.41.5-0+deb13u1` | 候选镜像已含回补 | Debian 修复 2.41.5-0+deb13u1；实际 dpkg 版本比较满足。（发行商：resolved） |
| util-linux | [CVE-2026-76642](https://security-tracker.debian.org/tracker/CVE-2026-76642) | `2.41.5-0+deb13u1` | 组件残余／未发现应用可达 | 本机 login／chfn／chsh／mount／nsenter 路径；GEP 不调用这些命令，无新权限且应用移除全部 Linux capabilities。（发行商：open） |
| util-linux | [CVE-2026-78408](https://security-tracker.debian.org/tracker/CVE-2026-78408) | `2.41.5-0+deb13u1` | 组件残余／未发现应用可达 | 本机 login／chfn／chsh／mount／nsenter 路径；GEP 不调用这些命令，无新权限且应用移除全部 Linux capabilities。（发行商：open） |
| util-linux | [CVE-2026-78409](https://security-tracker.debian.org/tracker/CVE-2026-78409) | `2.41.5-0+deb13u1` | 组件残余／未发现应用可达 | 本机 login／chfn／chsh／mount／nsenter 路径；GEP 不调用这些命令，无新权限且应用移除全部 Linux capabilities。（发行商：open） |
| util-linux | [CVE-2026-78410](https://security-tracker.debian.org/tracker/CVE-2026-78410) | `2.41.5-0+deb13u1` | 组件残余／未发现应用可达 | 本机 login／chfn／chsh／mount／nsenter 路径；GEP 不调用这些命令，无新权限且应用移除全部 Linux capabilities。（发行商：open） |
| zlib | [CVE-2026-27171](https://security-tracker.debian.org/tracker/CVE-2026-27171) | `1:1.3.dfsg+really1.3.1-1` | 组件残余／未发现应用可达 | 需 crc32_combine64／crc32_combine_gen64 特定参数；GEP 不调用组合 CRC API。（发行商：open） |
| zlib | [CVE-2026-85091](https://security-tracker.debian.org/tracker/CVE-2026-85091) | `1:1.3.dfsg+really1.3.1-1` | 组件残余／未发现应用可达 | 需特定版本与非阻塞 gzwrite 缓冲；Debian 有争议记录，GEP 不调用该 gz* 接口。（发行商：open） |

## 代理另行核对

原官方 Caddy 2.11.7 镜像的 Go 1.26.8／x/net 0.59.0 匹配 15 个不同 Go 公告；修补代理保留 Caddy 2.11.7，以 Go 1.26.10／x/net 0.60.0 重建。最终代理的 Go／Alpine 168 项查询仍命中 OpenPGP 整模块停维公告 GO-2026-5932，govulncheck binary 检查也因此非零，失败保留。实际 ELF 的 62,595 个 Go 函数中没有 OpenPGP 函数，同版标准入口的 1,016 个导入包也没有该包，按“模块存在、当前构建未发现调用”人工复核，不将工具结果改成零。细节与限制见主审计报告；新增插件或改变入口须重新核对。

数据源：[Debian 官方跟踪器](https://security-tracker.debian.org/tracker/)、[OSV API](https://google.github.io/osv.dev/api/)、[Go 官方漏洞库](https://vuln.go.dev/)。扫描时只发送公开包名与版本；没有发送源码、数据库、凭据或参与者记录。

