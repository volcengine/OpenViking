Tool: cancel_booking

Static Description:
"取消指定的已预订航班"

- Success rate: 100% (1/1)
- When to use: 当用户需要取消已有的航班预订时使用
- Optimal params: booking_id参数使用预订成功后返回的唯一预订编号
- Common failures: 预订编号错误会导致取消失败，已起飞的航班无法取消
- Recommendation: 调用前需确认用户要取消的预订编号准确，且航班尚未起飞

<!-- MEMORY_FIELDS
{
  "tool_name": "cancel_booking",
  "static_desc": "取消指定的已预订航班",
  "call_count": 1,
  "success_time": 1,
  "when_to_use": "当用户需要取消已有的航班预订时使用",
  "optimal_params": "booking_id参数使用预订成功后返回的唯一预订编号",
  "common_failures": "预订编号错误会导致取消失败，已起飞的航班无法取消",
  "recommendation": "调用前需确认用户要取消的预订编号准确，且航班尚未起飞",
  "guidelines": "",
  "user_id": "alice",
  "memory_type": "tools"
}
-->