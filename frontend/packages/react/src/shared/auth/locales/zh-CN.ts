import type { AuthMessages } from '../types.js';

const messages = {
  email: '邮箱',
  password: '密码',
  apiKey: 'API Key',
  newPassword: '新密码',
  confirmation: '确认新密码',
  showPassword: '显示密码',
  hidePassword: '隐藏密码',
  required: '此项为必填项。',
  invalidEmail: '请输入有效的邮箱地址。',
  mismatch: '两次输入的密码不一致。',
  login: '登录',
  send: '发送重置邮件',
  resend: '重新发送邮件',
  confirm: '确认并登录',
  agreementPrefix: '我已阅读并同意',
  agreementTitle: '阅读并同意协议',
  agreementDescription: '请阅读并同意以下协议后继续。',
  agree: '同意并继续',
  cancel: '取消',
  account: '登录账号',
  acceptInvitation: '接受邀请',
} satisfies AuthMessages;

export default messages;
