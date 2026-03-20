---
sidebar_position: 3
title: "Updating"
description: "Update or uninstall Gauss."
---

# Updating

## Update

```bash
gauss update
```

If you installed from a checked-out repository with `./scripts/install.sh`, `gauss update` pulls updates inside that same `math-inc/opengauss` checkout and refreshes the repository-local virtualenv and `mini-swe-agent` dependency.

## Uninstall

```bash
gauss uninstall
```

Gauss can remove the code while keeping your config/data for later reinstall.
