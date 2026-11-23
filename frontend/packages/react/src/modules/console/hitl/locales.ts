import type { Locale } from '../../../shared/locale.js';

const enUS = {
  empty: 'Not provided',
  required: 'Complete this field',
  nameInvalid:
    'Use up to 64 letters, numbers, underscores or hyphens, or up to 32 characters with Chinese ideographs. The name cannot start with a hyphen.',
  uriInvalid: 'Enter an OpenViking URI beginning with viking://',
  weekly: (weekday: string, time: string) => `Every ${weekday} ${time}`,
  daily: (time: string) => `Daily ${time}`,
  directory: 'Directory',
  sourceUriHint: 'Enter one Viking URI per line.',
  targetOverlap:
    'The output directory must be separate from every source directory.',
  targetSkillNamespace: 'Choose the skills namespace root.',
  targetNamespaceRoot:
    'Choose a directory inside the resource or memory namespace.',
  targetNotDirectory:
    'This location already exists as a file. Choose a directory.',
  targetReadOnly:
    'This directory is not writable. Choose a different location.',
  targetCheckFailed: 'Could not check the output directory. Try again.',
  chooseSkill: 'Choose a skill',
  uploadSkill: 'Upload skill',
  chooseSkillFile: 'Choose or drop a SKILL.md file',
  skillFileInvalid: 'Select one file named SKILL.md.',
  skillFileUnreadable: 'Could not read SKILL.md. Choose the file again.',
  skillUploadMissingPath: 'The upload did not return a Skill path.',
  back: 'Back',
  customDirectory: 'Enter a directory URI',
  removeDirectory: (uri: string) => `Remove ${uri}`,
  titles: {
    compile_output_type: 'Output type',
    compile_source: 'Select the source directories to compile',
    compile_target: 'Output directory',
    compile_name: 'Task name',
    compile_skill: 'Compile skill',
    compile_instruction: 'Additional instructions',
    compile_schedule: 'Automatic updates',
  },
};
const zhCN: typeof enUS = {
  empty: '未填写',
  required: '请填写此项',
  nameInvalid:
    '名称可使用字母、数字、下划线和连字符，最多 64 个字符。包含汉字时最多 32 个字符。名称不能以连字符开头。',
  uriInvalid: '请输入以 viking:// 开头的 OpenViking URI',
  weekly: (weekday, time) => `每${weekday} ${time}`,
  daily: (time) => `每日 ${time}`,
  directory: '目录',
  sourceUriHint: '每行输入一个 Viking URI。',
  targetOverlap: '输出目录不能与来源目录相同，也不能互相包含。',
  targetSkillNamespace: '请选择技能命名空间本身，请勿选择具体技能目录。',
  targetNamespaceRoot: '请选择资源或记忆命名空间下的目录。',
  targetNotDirectory: '此路径已存在同名文件，请选择目录。',
  targetReadOnly: '此目录不可写，请选择其他位置。',
  targetCheckFailed: '无法确认输出目录，请重试。',
  chooseSkill: '选择技能',
  uploadSkill: '上传技能',
  chooseSkillFile: '点击或拖拽 SKILL.md 文件到此处上传',
  skillFileInvalid: '请选择一个名为 SKILL.md 的文件。',
  skillFileUnreadable: '无法读取 SKILL.md，请重新选择文件。',
  skillUploadMissingPath: '上传结果缺少技能路径。',
  back: '返回',
  customDirectory: '输入目录 URI',
  removeDirectory: (uri) => `移除 ${uri}`,
  titles: {
    compile_output_type: '产物类型',
    compile_source: '请选择需要编译的数据源目录',
    compile_target: '输出目录',
    compile_name: '任务名称',
    compile_skill: '编译技能',
    compile_instruction: '补充要求',
    compile_schedule: '自动更新',
  },
};
export type CompileCardType = keyof typeof enUS.titles;
export const hitlLabels = (locale: Locale) =>
  locale === 'zh-CN' ? zhCN : enUS;
