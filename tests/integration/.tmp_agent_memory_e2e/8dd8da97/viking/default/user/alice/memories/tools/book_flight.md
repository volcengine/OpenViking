Tool: book_flight

Static Description:
"为指定乘客预订选定的航班"

- Success rate: 50% (2/4)
- When to use: 当用户确认选定航班后，为其完成机票预订时使用
- Optimal params: flight参数使用航班号，passenger参数使用用户唯一标识
- Common failures: 用户已存在相同日期的预订会导致订票失败，乘客信息错误会导致预订失败
- Recommendation: 调用前需检查用户是否已有相同日期的航班预订，确认乘客信息准确

<!-- MEMORY_FIELDS
{
  "tool_name": "book_flight",
  "static_desc": "为指定乘客预订选定的航班",
  "call_count": 4,
  "success_time": 2,
  "when_to_use": "当用户确认选定航班后，为其完成机票预订时使用",
  "optimal_params": "flight参数使用航班号，passenger参数使用用户唯一标识",
  "common_failures": "用户已存在相同日期的预订会导致订票失败，乘客信息错误会导致预订失败",
  "recommendation": "调用前需检查用户是否已有相同日期的航班预订，确认乘客信息准确",
  "guidelines": "",
  "user_id": "alice",
  "memory_type": "tools"
}
-->