"""
测试工具模块
提供 Session ID 管理、智能等待、重试机制、测试数据管理等功能
"""

import functools
import logging
import os
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional, TypeVar, Union

logger = logging.getLogger(__name__)

T = TypeVar("T")

# 固定的 Session ID，用于 CI 环境避免 session 爆满
# 使用 UUID 格式，确保 OpenClaw 直接使用，不做 SHA256 转换
FIXED_SESSION_ID = "00000000-0000-0000-0000-000000000001"

# CI 环境下基于名称生成确定性 UUID 的命名空间
_CI_SESSION_NAMESPACE = uuid.UUID("6ba7b810-9dad-11d1-80b4-00c04fd430c8")


def _ci_deterministic_uuid(name: str) -> str:
    """基于名称生成确定性 UUID，CI 环境下同一名称始终返回同一 UUID。"""
    return str(uuid.uuid5(_CI_SESSION_NAMESPACE, name))


def _is_ci_environment() -> bool:
    """检测是否在 CI 环境中运行"""
    return bool(
        os.environ.get("CI")
        or os.environ.get("GITHUB_ACTIONS")
        or os.environ.get("GITLAB_CI")
        or os.environ.get("TRAVIS")
        or os.environ.get("CIRCLECI")
        or os.environ.get("JENKINS_URL")
    )


class SessionIdManager:
    """
    Session ID 管理器
    自动生成唯一的 session_id，支持前缀和后缀
    在 CI 环境中使用固定的 session ID，避免 session 爆满
    """

    _instance = None
    _session_registry: Dict[str, Dict[str, Any]] = {}

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    @staticmethod
    def generate_session_id(
        prefix: str = "test",
        include_timestamp: bool = True,
        include_uuid: bool = True,
    ) -> str:
        """
        生成唯一的 session_id (UUID 格式)

        OpenClaw 会将非 UUID 格式的 session ID 转换为 SHA256 哈希，
        使用 UUID 格式可以确保 OpenClaw 直接使用，不做转换。

        Args:
            prefix: session_id 前缀 (仅用于日志，不影响 UUID 格式)
            include_timestamp: 是否包含时间戳 (已忽略，保持接口兼容)
            include_uuid: 是否包含 UUID (已忽略，始终使用 UUID)

        Returns:
            str: UUID 格式的 session_id
        """
        # 在 CI 环境中使用基于前缀的确定性 session ID，避免不同测试互相干扰
        if _is_ci_environment():
            session_id = _ci_deterministic_uuid(f"session:{prefix}")
            logger.info(f"CI 环境检测到，使用确定性 Session ID: {session_id} (prefix: {prefix})")
            return session_id

        # 生成 UUID 格式的 session ID
        session_id = str(uuid.uuid4())
        logger.info(f"生成 Session ID: {session_id} (prefix: {prefix})")
        return session_id

    @staticmethod
    def generate_test_class_session_id(test_class_name: str) -> str:
        """
        为测试类生成 session_id

        Args:
            test_class_name: 测试类名称

        Returns:
            str: 唯一的 session_id
        """
        # 在 CI 环境中使用基于类名的确定性 session ID，避免不同测试类互相干扰
        if _is_ci_environment():
            session_id = _ci_deterministic_uuid(f"class:{test_class_name}")
            logger.info(
                f"CI 环境检测到，使用确定性 Session ID: {session_id} (class: {test_class_name})"
            )
            return session_id

        return f"test_{test_class_name}_{uuid.uuid4().hex[:8]}"

    @staticmethod
    def generate_test_method_session_id(test_class_name: str, test_method_name: str) -> str:
        """
        为测试方法生成 session_id

        Args:
            test_class_name: 测试类名称
            test_method_name: 测试方法名称

        Returns:
            str: 唯一的 session_id
        """
        # 在 CI 环境中使用基于类名+方法名的确定性 session ID
        if _is_ci_environment():
            session_id = _ci_deterministic_uuid(f"method:{test_class_name}:{test_method_name}")
            logger.info(
                f"CI 环境检测到，使用确定性 Session ID: {session_id} "
                f"(class: {test_class_name}, method: {test_method_name})"
            )
            return session_id

        return f"test_{test_class_name}_{test_method_name}_{uuid.uuid4().hex[:8]}"

    def register_session(
        self,
        session_id: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        """
        注册 session

        Args:
            session_id: session ID
            metadata: session 元数据
        """
        self._session_registry[session_id] = {
            "created_at": datetime.now().isoformat(),
            "metadata": metadata or {},
        }
        logger.info(f"注册 session: {session_id}")

    def get_session_info(self, session_id: str) -> Optional[Dict[str, Any]]:
        """
        获取 session 信息

        Args:
            session_id: session ID

        Returns:
            Optional[Dict[str, Any]]: session 信息
        """
        return self._session_registry.get(session_id)

    def cleanup_session(self, session_id: str) -> None:
        """
        清理 session

        Args:
            session_id: session ID
        """
        if session_id in self._session_registry:
            del self._session_registry[session_id]
            logger.info(f"清理 session: {session_id}")

    def get_all_sessions(self) -> Dict[str, Dict[str, Any]]:
        """
        获取所有 session

        Returns:
            Dict[str, Dict[str, Any]]: 所有 session
        """
        return self._session_registry.copy()


class SmartWaiter:
    """
    智能等待策略
    支持轮询检查、超时控制、指数退避
    """

    def __init__(
        self,
        default_timeout: float = 60.0,
        default_poll_interval: float = 1.0,
        max_poll_interval: float = 10.0,
        exponential_backoff: bool = True,
        backoff_factor: float = 2.0,
    ):
        """
        初始化智能等待器

        Args:
            default_timeout: 默认超时时间（秒）
            default_poll_interval: 默认轮询间隔（秒）
            max_poll_interval: 最大轮询间隔（秒）
            exponential_backoff: 是否使用指数退避
            backoff_factor: 退避因子
        """
        self.default_timeout = default_timeout
        self.default_poll_interval = default_poll_interval
        self.max_poll_interval = max_poll_interval
        self.exponential_backoff = exponential_backoff
        self.backoff_factor = backoff_factor

    def wait_for_condition(
        self,
        condition: Callable[[], bool],
        timeout: Optional[float] = None,
        poll_interval: Optional[float] = None,
        message: str = "等待条件满足",
    ) -> bool:
        """
        等待条件满足

        Args:
            condition: 条件函数，返回 True 表示条件满足
            timeout: 超时时间（秒）
            poll_interval: 轮询间隔（秒）
            message: 等待消息

        Returns:
            bool: 条件是否在超时前满足
        """
        timeout = timeout or self.default_timeout
        poll_interval = poll_interval or self.default_poll_interval

        start_time = time.time()
        current_interval = poll_interval
        attempt = 0

        logger.info(f"开始等待: {message} (超时: {timeout}秒)")

        while time.time() - start_time < timeout:
            attempt += 1

            try:
                if condition():
                    elapsed = time.time() - start_time
                    logger.info(
                        f"✅ 条件满足: {message} (耗时: {elapsed:.2f}秒, 尝试次数: {attempt})"
                    )
                    return True
            except Exception as e:
                logger.warning(f"条件检查异常 (尝试 {attempt}): {e}")

            if self.exponential_backoff:
                current_interval = min(
                    current_interval * self.backoff_factor,
                    self.max_poll_interval,
                )

            time.sleep(current_interval)

        elapsed = time.time() - start_time
        logger.warning(f"❌ 等待超时: {message} (耗时: {elapsed:.2f}秒, 尝试次数: {attempt})")
        return False

    def wait_for_response_keywords(
        self,
        get_response: Callable[[], Dict[str, Any]],
        keywords: List[str],
        timeout: Optional[float] = None,
        poll_interval: Optional[float] = None,
        require_all: bool = True,
        case_sensitive: bool = False,
    ) -> bool:
        """
        等待响应中包含指定关键词

        Args:
            get_response: 获取响应的函数
            keywords: 关键词列表
            timeout: 超时时间（秒）
            poll_interval: 轮询间隔（秒）
            require_all: 是否要求所有关键词都出现
            case_sensitive: 是否区分大小写

        Returns:
            bool: 是否在超时前找到关键词
        """
        from utils.assertions import AssertionHelper

        def check_keywords() -> bool:
            response = get_response()
            return AssertionHelper.assert_keywords_in_response(
                response, keywords, require_all, case_sensitive
            )

        return self.wait_for_condition(
            check_keywords,
            timeout=timeout,
            poll_interval=poll_interval,
            message=f"等待响应包含关键词: {keywords}",
        )

    def smart_wait(
        self,
        base_wait: float = 5.0,
        max_wait: float = 30.0,
        adaptive: bool = True,
    ) -> float:
        """
        智能等待，根据历史响应时间调整等待时间

        Args:
            base_wait: 基础等待时间（秒）
            max_wait: 最大等待时间（秒）
            adaptive: 是否自适应调整

        Returns:
            float: 实际等待时间
        """
        wait_time = base_wait

        if adaptive:
            wait_time = min(wait_time * 1.2, max_wait)

        logger.info(f"智能等待 {wait_time:.1f} 秒...")
        time.sleep(wait_time)
        return wait_time


class RetryManager:
    """
    重试机制
    支持自定义重试条件、指数退避、最大重试次数
    """

    def __init__(
        self,
        max_retries: int = 3,
        base_delay: float = 1.0,
        max_delay: float = 60.0,
        exponential_backoff: bool = True,
        backoff_factor: float = 2.0,
    ):
        """
        初始化重试管理器

        Args:
            max_retries: 最大重试次数
            base_delay: 基础延迟（秒）
            max_delay: 最大延迟（秒）
            exponential_backoff: 是否使用指数退避
            backoff_factor: 退避因子
        """
        self.max_retries = max_retries
        self.base_delay = base_delay
        self.max_delay = max_delay
        self.exponential_backoff = exponential_backoff
        self.backoff_factor = backoff_factor

    def retry_on_exception(
        self,
        exceptions: Union[type, tuple] = Exception,
        on_retry: Optional[Callable[[int, Exception], None]] = None,
    ) -> Callable:
        """
        装饰器：在指定异常时重试

        Args:
            exceptions: 要捕获的异常类型
            on_retry: 重试时的回调函数

        Returns:
            Callable: 装饰器函数
        """

        def decorator(func: Callable[..., T]) -> Callable[..., T]:
            @functools.wraps(func)
            def wrapper(*args, **kwargs) -> T:
                last_exception = None
                delay = self.base_delay

                for attempt in range(self.max_retries + 1):
                    try:
                        return func(*args, **kwargs)
                    except exceptions as e:
                        last_exception = e

                        if attempt < self.max_retries:
                            if on_retry:
                                on_retry(attempt + 1, e)

                            logger.warning(
                                f"重试 {attempt + 1}/{self.max_retries}: {func.__name__} - {e}"
                            )
                            time.sleep(delay)

                            if self.exponential_backoff:
                                delay = min(delay * self.backoff_factor, self.max_delay)
                        else:
                            logger.error(f"重试次数耗尽: {func.__name__} - {e}")
                            raise

                raise last_exception

            return wrapper

        return decorator

    def retry_on_result(
        self,
        condition: Callable[[Any], bool],
        max_retries: Optional[int] = None,
    ) -> Callable:
        """
        装饰器：在结果满足条件时重试

        Args:
            condition: 条件函数，返回 True 表示需要重试
            max_retries: 最大重试次数（覆盖默认值）

        Returns:
            Callable: 装饰器函数
        """
        retries = max_retries or self.max_retries

        def decorator(func: Callable[..., T]) -> Callable[..., T]:
            @functools.wraps(func)
            def wrapper(*args, **kwargs) -> T:
                delay = self.base_delay
                last_result = None

                for attempt in range(retries + 1):
                    result = func(*args, **kwargs)
                    last_result = result

                    if not condition(result):
                        return result

                    if attempt < retries:
                        logger.warning(
                            f"结果不满足条件，重试 {attempt + 1}/{retries}: {func.__name__}"
                        )
                        time.sleep(delay)

                        if self.exponential_backoff:
                            delay = min(delay * self.backoff_factor, self.max_delay)
                    else:
                        logger.warning(f"重试次数耗尽，返回最后结果: {func.__name__}")

                return last_result

            return wrapper

        return decorator

    def execute_with_retry(
        self,
        func: Callable[..., T],
        *args,
        exceptions: Union[type, tuple] = Exception,
        **kwargs,
    ) -> T:
        """
        执行函数并在异常时重试

        Args:
            func: 要执行的函数
            *args: 函数参数
            exceptions: 要捕获的异常类型
            **kwargs: 函数关键字参数

        Returns:
            T: 函数返回值
        """

        @self.retry_on_exception(exceptions)
        def wrapped():
            return func(*args, **kwargs)

        return wrapped()


@dataclass
class TestData:
    """
    测试数据类
    用于管理测试数据
    """

    name: str
    description: str = ""
    input_data: Dict[str, Any] = field(default_factory=dict)
    expected_keywords: List[List[str]] = field(default_factory=list)
    expected_similarity: Optional[str] = None
    min_similarity: float = 0.6
    tags: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)


class TestDataManager:
    """
    测试数据管理器
    支持从配置文件加载、数据验证、数据驱动测试
    """

    def __init__(self):
        self._data_registry: Dict[str, TestData] = {}

    def register_data(self, data: TestData) -> None:
        """
        注册测试数据

        Args:
            data: 测试数据
        """
        self._data_registry[data.name] = data
        logger.info(f"注册测试数据: {data.name}")

    def get_data(self, name: str) -> Optional[TestData]:
        """
        获取测试数据

        Args:
            name: 数据名称

        Returns:
            Optional[TestData]: 测试数据
        """
        return self._data_registry.get(name)

    def get_all_data(self) -> Dict[str, TestData]:
        """
        获取所有测试数据

        Returns:
            Dict[str, TestData]: 所有测试数据
        """
        return self._data_registry.copy()

    def get_data_by_tag(self, tag: str) -> List[TestData]:
        """
        根据标签获取测试数据

        Args:
            tag: 标签

        Returns:
            List[TestData]: 匹配的测试数据列表
        """
        return [data for data in self._data_registry.values() if tag in data.tags]

    def validate_data(self, data: TestData) -> bool:
        """
        验证测试数据

        Args:
            data: 测试数据

        Returns:
            bool: 是否有效
        """
        if not data.name:
            logger.error("测试数据名称不能为空")
            return False

        if not data.input_data:
            logger.warning(f"测试数据 {data.name} 没有输入数据")

        return True


DEFAULT_TEST_DATA = {
    "user_xiaoming": TestData(
        name="user_xiaoming",
        description="测试用户小明",
        input_data={
            "message": "我叫小明，今年30岁，住在华东区，职业是测试开发",
        },
        expected_keywords=[
            ["小明", "测试开发", "30岁", "华东"],
        ],
        tags=["user", "basic"],
    ),
    "user_xiaohong": TestData(
        name="user_xiaohong",
        description="测试用户小红",
        input_data={
            "message": (
                "我叫小红，今年25岁，住在华北区北京市朝阳区，职业是产品经理，"
                "喜欢美食和旅游，不喜欢加班，我的生日是1999年8月15日"
            ),
        },
        expected_keywords=[
            ["产品经理"],
            ["1999", "8月", "8/15"],
            ["美食", "旅游"],
        ],
        tags=["user", "rich"],
    ),
    "fruit_cherry": TestData(
        name="fruit_cherry",
        description="水果偏好 - 樱桃",
        input_data={
            "message": "我喜欢吃樱桃，日常喜欢喝美式咖啡",
        },
        expected_keywords=[
            ["樱桃"],
            ["美式", "咖啡"],
        ],
        tags=["fruit", "drink"],
    ),
    "fruit_mango": TestData(
        name="fruit_mango",
        description="水果偏好 - 芒果",
        input_data={
            "message": "我喜欢吃芒果，日常喜欢喝拿铁咖啡",
        },
        expected_keywords=[
            ["芒果"],
            ["拿铁", "咖啡"],
        ],
        tags=["fruit", "drink"],
    ),
    "fruit_strawberry": TestData(
        name="fruit_strawberry",
        description="水果偏好 - 草莓",
        input_data={
            "message": "我喜欢吃草莓，日常喜欢喝抹茶拿铁",
        },
        expected_keywords=[
            ["草莓"],
            ["抹茶", "拿铁"],
        ],
        tags=["fruit", "drink"],
    ),
}


def get_default_data_manager() -> TestDataManager:
    """
    获取默认的测试数据管理器

    Returns:
        TestDataManager: 测试数据管理器
    """
    manager = TestDataManager()
    for data in DEFAULT_TEST_DATA.values():
        manager.register_data(data)
    return manager
