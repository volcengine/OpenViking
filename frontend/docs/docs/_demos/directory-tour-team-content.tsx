import { Button } from '#demo-controls';
import {
  ArrowUpRightIcon,
  DirectoryTourFolderIcon,
} from '@openviking/react/icons';
import { withBase } from '@rspress/core/runtime';
import { DirectoryTourIntro } from '@openviking/react/components/directory-tour';

type Props = {
  locale: 'zh-CN' | 'en-US';
  onDone: () => void;
  onAction: (action: 'video' | 'edit') => void;
};

export function TeamTourContent({ locale, onDone, onAction }: Props) {
  const zh = locale === 'zh-CN';
  const title = zh ? '团队经验' : 'Team experiences';
  return (
    <DirectoryTourIntro
      locale={locale}
      title={title}
      onDone={onDone}
      media={
        <Button
          className="ov-directory-tour-team__video"
          htmlType="button"
          aria-label={
            zh ? '播放团队经验介绍视频' : 'Play team experiences introduction'
          }
          onClick={() => onAction('video')}
        >
          <img
            src={withBase('/directory-tour/team/cover.png')}
            width={246}
            height={159}
            alt=""
          />
        </Button>
      }
      description={
        zh ? (
          <>
            将团队成员各自{' '}
            <DirectoryTourFolderIcon className="ov-directory-tour-team__folder" />{' '}
            <span className="ov-directory-tour-team__term">experiences</span>{' '}
            目录下的文档沉淀为可共享、可复用的团队经验库，成员提问时，会优先检索团队经验库中的内容作答，提升团队协作效率。
          </>
        ) : (
          <>
            Turn each member’s experiences into shared, reusable team knowledge.
            Agents search this knowledge first to answer questions and improve
            collaboration.
          </>
        )
      }
      features={[
        zh
          ? '让团队经验持续沉淀、随问随用'
          : 'Keep team experience available on demand',
        zh ? '全员共同编辑，持续维护' : 'Edit and maintain together',
        <span
          key="rules"
          className="ov-directory-tour-team__rules"
          lang={locale}
        >
          {zh
            ? '成员可编辑敏感信息的读取规则'
            : 'Members can edit sensitive-data access rules'}{' '}
          <Button
            htmlType="button"
            className="ov-directory-tour-team__edit"
            onClick={() => onAction('edit')}
          >
            {zh ? '编辑' : 'Edit'}
            <ArrowUpRightIcon />
          </Button>
        </span>,
      ]}
    />
  );
}
