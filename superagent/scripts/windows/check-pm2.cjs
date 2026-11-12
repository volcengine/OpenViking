// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0
// Loading PM2 must not start a daemon during package installation.
require('pm2');
console.log('Bundled PM2 loads');
