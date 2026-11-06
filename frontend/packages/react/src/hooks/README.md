# Cross-module Hooks

English / [中文](README_CN.md)

Reserved for Hooks genuinely shared by more than one module; implementations are added when real reuse appears.

Public Hooks are exposed from `@openviking/react/hooks` only once they have a real implementation, and they ship no CSS. Public Hooks specific to a business module stay in `modules/<module>/` and are exported from that module's entry so composite views and hosts can reuse them independently; they are not promoted here. Private Hooks stay inside their module, and Hooks used by components follow the same view-layer boundary — data loading and business orchestration belong to module Hooks or the host.

This directory currently holds only this description; there are no Hook implementations or public entries.

Development and build entry points are in the [UI workspace README](../../../../README.md); module conventions are in the [package README](../../README.md).
