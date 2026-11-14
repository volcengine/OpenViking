export type BlockName = 'FileBrowser' | 'Console' | 'ScopeCascade';

export type RegionByComponent = {
  FileBrowserActionBar: 'root';
  FileTree: 'root';
  ContextPicker: 'root';
  FilePreview:
    | 'root'
    | 'content'
    | 'source'
    | 'viewer-loader'
    | 'editor-loader'
    | 'image'
    | 'image-dialog'
    | 'video';
  CommandConsole: 'root' | 'record';
  ScopeSwitcher: 'root';
  ScopeSelector: 'root';
  AgentConnect: 'root';
  VikingBot: 'root' | 'composer' | 'message-list' | 'interaction';
  DirectoryTour: 'root';
  WikiDashboard: 'root';
  ExperienceDashboard: 'root';
  EmailLoginForm: 'root';
  ApiKeyLoginForm: 'root';
  PasswordResetRequestForm: 'root';
  PasswordSetupForm: 'root';
  LoginMethodSelector: 'root';
  InvitationConfirmation: 'root';
  FileBrowser:
    | 'root'
    | 'directory'
    | 'preview'
    | 'search'
    | 'wiki-dashboard'
    | 'experience-dashboard'
    | 'trajectory';
  Console:
    'root' | 'commands' | 'chat' | 'sessions' | 'task-tracking' | 'persistence';
  ScopeCascade: 'root' | 'libraries' | 'spaces' | 'users';
};

export type ComponentName = keyof RegionByComponent;

export type ComponentRef<N extends ComponentName = ComponentName> = {
  [K in N]: {
    readonly name: K;
    readonly instanceId: string;
    readonly region: RegionByComponent[K];
  };
}[N];

export type BlockRef<N extends BlockName = BlockName> = {
  readonly name: N;
  readonly instanceId: string;
};

export type Source<N extends ComponentName = ComponentName> = {
  [K in N]: {
    readonly component: ComponentRef<K>;
    readonly module: K extends BlockName ? BlockRef<K> : BlockRef | null;
    readonly componentPath: readonly [...ComponentRef[], ComponentRef<K>];
    readonly affectedComponents: readonly ComponentRef[];
  };
}[N];
