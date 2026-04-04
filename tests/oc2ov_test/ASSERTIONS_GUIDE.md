# 断言使用指南

本项目提供了多种断言方式来验证 OpenClaw 的响应。

## 断言方法

### 1. 关键词断言

#### `assertKeywordsInResponse()`
断言响应中包含指定的所有关键词。

**参数：**
- `response`: 响应内容（字典或字符串）
- `keywords`: 关键词列表
- `require_all`: 是否要求所有关键词都必须出现，默认 `True`
- `case_sensitive`: 是否区分大小写，默认 `False`
- `msg`: 自定义错误信息

**示例：**

```python
# 验证响应中包含所有关键词
self.assertKeywordsInResponse(
    response,
    ["小明", "30岁", "测试开发"],
    require_all=True,
    case_sensitive=False
)

# 验证响应中包含任意一个关键词
self.assertKeywordsInResponse(
    response,
    ["30", "三十"],
    require_all=False
)
```

---

### 2. 任意关键词组断言

#### `assertAnyKeywordInResponse()`
断言响应中包含任意一组关键词中的任意一个。

**参数：**
- `response`: 响应内容
- `keyword_groups`: 关键词组列表（二维数组）
- `case_sensitive`: 是否区分大小写，默认 `False`
- `msg`: 自定义错误信息

**示例：**

```python
# 验证响应中包含姓名、年龄或职业中的任意一个
self.assertAnyKeywordInResponse(
    response,
    [
        ["小明", "小红"],      # 第一组：姓名
        ["30", "25", "28"],   # 第二组：年龄
        ["测试开发", "产品经理"]  # 第三组：职业
    ],
    case_sensitive=False
)
```

---

### 3. 文本相似度断言

#### `assertSimilarity()`
断言响应文本与期望文本的相似度达到指定阈值。

**参数：**
- `response`: 响应内容
- `expected_text`: 期望的文本
- `min_similarity`: 最小相似度阈值，范围 0.0-1.0，默认 0.6
- `msg`: 自定义错误信息

**示例：**

```python
# 验证响应文本与期望文本相似度 >= 70%
self.assertSimilarity(
    response,
    "你叫小明，今年30岁，住在华东区，职业是测试开发",
    min_similarity=0.7
)
```

---

## 实用技巧

### 组合使用断言

```python
def test_some_scenario(self):
    # 发送消息
    response = self.send_and_log("我叫张三")
    self.wait_for_sync()
    
    # 验证响应
    verify_resp = self.send_and_log("我是谁")
    
    # 方式1：关键词断言
    self.assertKeywordsInResponse(verify_resp, ["张三"])
    
    # 方式2：任意关键词组断言（更灵活）
    self.assertAnyKeywordInResponse(
        verify_resp,
        [["张三", "小张"]]
    )
```

### 容错性设计

```python
# 提供多种可能的表述方式
self.assertAnyKeywordInResponse(
    response,
    [
        ["30岁", "30", "三十岁", "三十"],  # 年龄的多种表述
        ["测试开发", "测试工程师", "QA"]      # 职业的多种表述
    ]
)
```

### 渐进式验证

```python
# 先写入信息
self.send_and_log("我叫李四，今年35岁")
self.wait_for_sync()

# 逐项验证
name_resp = self.send_and_log("我叫什么？")
self.assertKeywordsInResponse(name_resp, ["李四"])

age_resp = self.send_and_log("我几岁？")
self.assertKeywordsInResponse(age_resp, ["35", "三十五"])
```

---

## 直接使用 AssertionHelper

也可以直接使用 `AssertionHelper` 类进行断言：

```python
from utils.assertions import AssertionHelper

helper = AssertionHelper()

# 提取响应文本
text = helper.extract_response_text(response)

# 计算相似度
similarity = helper.calculate_similarity(text1, text2)

# 关键词检查（返回布尔值，不抛异常）
success = helper.assert_keywords_in_response(response, ["关键词"])
```

---

## 响应格式支持

`extract_response_text()` 方法支持多种响应格式：

- 纯字符串
- `{"output": "..."}`
- `{"message": "..."}`
- `{"content": "..."}`
- `{"text": "..."}`
- OpenAI 格式：`{"choices": [{"message": {"content": "..."}}]}`
- 其他格式会自动转为字符串

如果你的响应格式不被支持，可以扩展 `extract_response_text()` 方法。
