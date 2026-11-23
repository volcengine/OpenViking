export const notificationError = () =>
  document.querySelector(
    '.ov-notification-stack__item:not(.ov-notification-motion-exit):not(.ov-notification-motion-exit-active) .ov-notification--error',
  );

export const notificationContent = () =>
  notificationError()?.querySelector('.ov-notification__content');
export const toastContent = () =>
  document.querySelector(
    '.ov-toast-stack__item:not(.ov-toast-motion-exit):not(.ov-toast-motion-exit-active) .ov-toast__content',
  );
