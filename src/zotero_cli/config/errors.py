"""由加载器、命令与 CLI 边界共用的类型化配置错误。"""

from __future__ import annotations


class ConfigError(Exception):
    """配置缺失、不可读、格式错误或包含无效值。

    CLI 会将其映射为类型化的 `configuration_error`（退出码 3）；消息文本必须保持
    可操作，并且绝不能包含凭据值。
    """

    code = "configuration_error"
    retryable = False
