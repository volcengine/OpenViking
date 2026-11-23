export const authEvents = {
  email_login_form_show: {
    zh: '邮箱登录表单实际可见。',
    en: 'Email login form becomes visible.',
    fields: {},
  },
  email_login_form_submit: {
    zh: '邮箱登录表单通过本地校验后提交，不包含输入值，不代表认证成功。',
    en: 'Email login form is submitted after local validation; inputs are omitted and this does not imply authentication success.',
    fields: {},
  },
  api_key_login_form_show: {
    zh: 'API Key 登录表单实际可见。',
    en: 'API key login form becomes visible.',
    fields: {},
  },
  api_key_login_form_submit: {
    zh: 'API Key 登录表单通过本地校验后提交，不包含输入值，不代表认证成功。',
    en: 'API key login form is submitted after local validation; inputs are omitted and this does not imply authentication success.',
    fields: {},
  },
  password_reset_request_form_show: {
    zh: '重置密码申请表单实际可见。',
    en: 'Password reset request form becomes visible.',
    fields: {},
  },
  password_reset_request_form_submit: {
    zh: '重置密码申请表单通过本地校验后提交，不包含输入值，不代表认证成功。',
    en: 'Password reset request form is submitted after local validation; inputs are omitted and this does not imply authentication success.',
    fields: {},
  },
  password_setup_form_show: {
    zh: '设置密码表单实际可见。',
    en: 'Password setup form becomes visible.',
    fields: {},
  },
  password_setup_form_submit: {
    zh: '设置密码表单通过本地校验后提交，不包含输入值，不代表认证成功。',
    en: 'Password setup form is submitted after local validation; inputs are omitted and this does not imply authentication success.',
    fields: {},
  },
  login_method_selector_select: {
    zh: '用户选择登录方式，不包含账号信息。',
    en: 'The user selects a login method; account details are omitted.',
    fields: {},
  },
  invitation_confirmation_show: {
    zh: '邀请确认界面实际可见。',
    en: 'The invitation confirmation becomes visible.',
    fields: {},
  },
  invitation_confirmation_accept_click: {
    zh: '用户点击接受邀请，不代表接受成功。',
    en: 'The user clicks accept invitation; this does not imply acceptance succeeded.',
    fields: {},
  },
} as const;
