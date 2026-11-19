import { useLang } from '@rspress/core/runtime';
import { Button, useNotification, useToast } from '#demo-controls';
import '#toast-style.css';
import '#notification-style.css';

export default function ToastFeedback() {
  const zh = useLang() === 'zh';
  const locale = zh ? 'zh-CN' : 'en-US';
  const [toast, toastHolder] = useToast({ locale });
  const [notification, notificationHolder] = useNotification({ locale });
  return (
    <div>
      {toastHolder}
      {notificationHolder}
      <p>
        {zh
          ? '内部基础件演示：手动模拟反馈，不访问后端。'
          : 'Internal controls: simulated feedback, no backend requests.'}
      </p>
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8 }}>
        <Button
          onClick={() =>
            toast.warning({
              id: 'validation',
              content: zh ? '请选择导出目录' : 'Select a directory to export.',
            })
          }
        >
          {zh ? '前端校验' : 'Client validation'}
        </Button>
        <Button
          onClick={() =>
            notification.error({
              id: 'export-error',
              title: zh ? '导出文件失败' : 'File export failed',
              content: 'INVALID_ARGUMENT: Export requires a directory URI.',
              duration: 0,
            })
          }
        >
          {zh ? '模拟接口失败' : 'Simulate API failure'}
        </Button>
        <Button
          onClick={() =>
            toast.loading({
              id: 'operation',
              content: zh ? '正在检查文件…' : 'Checking files…',
            })
          }
        >
          {zh ? '加载提示' : 'Loading feedback'}
        </Button>
        <Button
          onClick={() =>
            toast.success({
              id: 'operation',
              content: zh ? '文件检查完成' : 'Files checked',
            })
          }
        >
          {zh ? '更新为成功' : 'Update to success'}
        </Button>
        <Button
          onClick={() =>
            toast.error({
              id: 'operation',
              content: zh
                ? '文件格式不受支持，请选择 .ovpack 文件'
                : 'Unsupported format. Select an .ovpack file.',
            })
          }
        >
          {zh ? '更新为失败' : 'Update to failure'}
        </Button>
        <Button
          onClick={() => {
            toast.clear();
            notification.clear();
          }}
        >
          {zh ? '清空提示' : 'Clear feedback'}
        </Button>
      </div>
    </div>
  );
}
