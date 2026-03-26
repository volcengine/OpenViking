#!/bin/bash
set -e

# 使用环境变量替换配置模板
envsubst < /etc/openviking/ov.conf.template > /etc/openviking/ov.conf

echo "Generated configuration:"
cat /etc/openviking/ov.conf

# 启动OpenViking服务
exec openviking server start
