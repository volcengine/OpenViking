Tool: search_flights

Static Description:
"查询指定日期、出发地、目的地和时间段的可用航班信息"

- Success rate: 100% (1/1)
- When to use: 当用户需要查询特定航线、日期和时段的航班信息时使用
- Optimal params: from参数使用机场三字码，to参数使用机场三字码，date参数使用YYYY-MM-DD格式，time参数可选morning/afternoon/evening
- Common failures: 日期格式错误会导致查询失败，机场代码错误会返回无结果
- Recommendation: 调用前需确认出发地、目的地、日期和时间段信息准确，优先使用机场三字码

<!-- MEMORY_FIELDS
{
  "tool_name": "search_flights",
  "static_desc": "查询指定日期、出发地、目的地和时间段的可用航班信息",
  "call_count": 1,
  "success_time": 1,
  "when_to_use": "当用户需要查询特定航线、日期和时段的航班信息时使用",
  "optimal_params": "from参数使用机场三字码，to参数使用机场三字码，date参数使用YYYY-MM-DD格式，time参数可选morning/afternoon/evening",
  "common_failures": "日期格式错误会导致查询失败，机场代码错误会返回无结果",
  "recommendation": "调用前需确认出发地、目的地、日期和时间段信息准确，优先使用机场三字码",
  "guidelines": "",
  "user_id": "alice",
  "memory_type": "tools"
}
-->