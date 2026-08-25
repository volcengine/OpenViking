package openviking

import (
	"net/http"
	"time"
)

// Config configures an HTTP OpenViking client.
type Config struct {
	BaseURL     string
	APIKey      string
	Account     string
	User        string
	ActorPeerID string
	Timeout     time.Duration

	ExtraHeaders map[string]string
	HTTPClient   *http.Client
	Profile      bool
	UploadMode   string
}

// AddResourceOptions controls AddResource.
type AddResourceOptions struct {
	To                  string
	Parent              string
	CreateParent        *bool
	AddType             string
	Reason              string
	Instruction         string
	Wait                bool
	Timeout             *float64
	Strict              bool
	IgnoreDirs          string
	Include             string
	Exclude             string
	DirectlyUploadMedia *bool
	PreserveStructure   *bool
	WatchInterval       float64
	ProcessingMode      string
	Args                map[string]any
	Tags                []string
	TagMode             string
	Telemetry           any
	Extra               map[string]any
}

// AddSkillOptions controls AddSkill.
type AddSkillOptions struct {
	Wait      bool
	Timeout   *float64
	Telemetry any
	Extra     map[string]any
	// TargetURI scopes the operation to a skills root such as
	// "viking://agent/skills" (account-shared) or a per-user root. A nil
	// value omits target_uri and lets the server use its default root.
	TargetURI any
}

// AdminCreateAccountOptions controls AdminCreateAccountWithOptions.
type AdminCreateAccountOptions struct {
	UserConfig map[string]any
	Seed       *string
}

// AdminRegisterUserOptions controls AdminRegisterUserWithOptions.
type AdminRegisterUserOptions struct {
	UserConfig map[string]any
	Seed       *string
}

// AdminRegenerateKeyOptions controls AdminRegenerateKeyWithOptions.
type AdminRegenerateKeyOptions struct {
	Seed *string
}

// ListSkillsOptions controls ListSkills.
type ListSkillsOptions struct {
	NodeLimit int
	TargetURI any
}

// FindSkillsOptions controls FindSkills.
type FindSkillsOptions struct {
	Limit          int
	ScoreThreshold *float64
	Level          []int
	Telemetry      any
	TargetURI      any
}

// ValidateSkillOptions controls ValidateSkill.
type ValidateSkillOptions struct {
	Strict       bool
	SourcePath   string
	SkillDirName string
	TargetURI    any
}

// GetSkillOptions controls GetSkill.
type GetSkillOptions struct {
	IncludeContent *bool
	IncludeFiles   *bool
	IncludeSource  bool
	Level          *int
	TargetURI      any
}

// UpdateSkillOptions controls UpdateSkill.
type UpdateSkillOptions struct {
	Wait           bool
	Timeout        *float64
	SourceMetadata map[string]any
	Telemetry      any
	TargetURI      any
	Extra          map[string]any
}

// DeleteSkillOptions controls DeleteSkill.
type DeleteSkillOptions struct {
	TargetURI any
}

// WaitProcessedOptions controls WaitProcessed.
type WaitProcessedOptions struct {
	Timeout *float64 `json:"timeout,omitempty"`
}

// ListWatchesOptions controls ListWatches.
type ListWatchesOptions struct {
	ActiveOnly bool
	ToURI      string
}

// WatchRef identifies a watch task by task ID or target URI.
type WatchRef struct {
	TaskID string
	ToURI  string
}

// UpdateWatchOptions controls UpdateWatch.
type UpdateWatchOptions struct {
	TaskID        string
	ToURI         string
	WatchInterval *float64
	IsActive      *bool
	Reason        *string
	Instruction   *string
}

// ListOptions controls List.
type ListOptions struct {
	Simple        bool
	Recursive     bool
	Output        string
	AbsLimit      int
	ShowAllHidden bool
	NodeLimit     int
	SortBy        string
	SortOrder     string
}

// TreeOptions controls Tree.
type TreeOptions struct {
	Output        string
	AbsLimit      int
	ShowAllHidden bool
	NodeLimit     int
	LevelLimit    *int
}

// RemoveOptions controls Remove.
type RemoveOptions struct {
	Recursive bool
	Wait      bool
	Timeout   *float64
}

// WriteOptions controls Write.
type WriteOptions struct {
	Mode           string
	Wait           bool
	Timeout        *float64
	Telemetry      any
	ProcessingMode string
	Extra          map[string]any
}

// BatchWriteOperation is one file write in a batch.
type BatchWriteOperation struct {
	URI           string  `json:"uri"`
	Content       *string `json:"content,omitempty"`
	ContentBase64 *string `json:"content_base64,omitempty"`
	Mode          string  `json:"mode,omitempty"`
}

// BatchWriteOptions controls BatchWrite.
type BatchWriteOptions struct {
	Wait      *bool
	Timeout   *float64
	Telemetry any
	Extra     map[string]any
}

// SetTagsOptions controls SetTags.
type SetTagsOptions struct {
	Mode      string
	Recursive bool
	Telemetry any
	Extra     map[string]any
}

// ReindexOptions controls Reindex.
// Wait is used as-is when options are provided; set it explicitly to true
// when adding optional fields such as Tags and synchronous behavior is desired.
type ReindexOptions struct {
	Mode      string
	Wait      bool
	DryRun    bool
	Recursive *bool
	Tags      []string
	TagMode   string
	Extra     map[string]any
}

// FindOptions controls Find.
type FindOptions struct {
	TargetURI         any
	Image             string
	Limit             int
	NodeLimit         *int
	ScoreThreshold    *float64
	Filter            map[string]any
	ContextType       any
	IncludeProvenance *bool
	ReadContent       *bool
	Telemetry         any
	Since             string
	Until             string
	TimeField         string
	Level             []int
	Tags              []string
	Extra             map[string]any
}

// SearchOptions controls Search.
type SearchOptions struct {
	TargetURI         any
	Image             string
	SessionID         string
	Limit             int
	NodeLimit         *int
	ScoreThreshold    *float64
	Filter            map[string]any
	ContextType       any
	IncludeProvenance *bool
	ReadContent       *bool
	Telemetry         any
	Since             string
	Until             string
	TimeField         string
	Level             []int
	Tags              []string
	Extra             map[string]any
}

// SearchContextOptions controls server-side context assembly.
type SearchContextOptions struct {
	Image             string
	SessionID         string
	Limit             *int
	NodeLimit         *int
	ScoreThreshold    *float64
	Filter            map[string]any
	ContextType       any
	IncludeProvenance *bool
	Tags              []string
	Since             string
	Until             string
	TimeField         string
	QueryExpansion    string
	MaxTokens         *int
	Quotas            map[string]int
	Purpose           string
	Detail            any
	DedupTurns        *int
	ExcludeURIs       []string
	PeerScope         string
	OtherPeerPenalty  any
	Rewrite           any
	RewriteMaxBullets *int
	Telemetry         any
	Extra             map[string]any
}

// GrepOptions controls Grep.
type GrepOptions struct {
	CaseInsensitive bool
	NodeLimit       *int
	LevelLimit      *int
	ExcludeURI      string
}

// GlobOptions controls Glob.
type GlobOptions struct {
	NodeLimit *int
}

// CreateSessionOptions controls CreateSession.
type CreateSessionOptions struct {
	SessionID              string
	MemoryPolicy           map[string]any
	AutoCommitPolicy       map[string]any
	DisableAutoCommit      bool
	MemoryExtractionConfig map[string]any
	Telemetry              any
	Extra                  map[string]any
}

// GetSessionOptions controls GetSession.
type GetSessionOptions struct {
	AutoCreate bool
}

// UpdateSessionConfigOptions controls UpdateSessionConfig.
type UpdateSessionConfigOptions struct {
	MemoryExtractionConfig map[string]any
	AutoCommitPolicy       *map[string]any
	Telemetry              any
	Extra                  map[string]any
}

// AddMessageOptions controls AddMessage.
type AddMessageOptions struct {
	Content          *string
	Parts            []map[string]any
	CreatedAt        string
	PeerID           string
	TurnID           string
	MessageKind      string
	SourceMessageIDs []string
	Telemetry        any
	Extra            map[string]any
}

// Message is one session message payload for BatchAddMessages.
type Message struct {
	Role             string           `json:"role"`
	Content          *string          `json:"content,omitempty"`
	Parts            []map[string]any `json:"parts,omitempty"`
	CreatedAt        string           `json:"created_at,omitempty"`
	PeerID           string           `json:"peer_id,omitempty"`
	TurnID           string           `json:"turn_id,omitempty"`
	MessageKind      string           `json:"message_kind,omitempty"`
	SourceMessageIDs []string         `json:"source_message_ids,omitempty"`
	Telemetry        any              `json:"telemetry,omitempty"`
}

// BatchAddMessagesOptions controls BatchAddMessages.
type BatchAddMessagesOptions struct {
	Telemetry any
	Extra     map[string]any
}

// CommitSessionOptions controls CommitSession.
type CommitSessionOptions struct {
	KeepRecentCount            *int
	RetentionMode              string
	KeepRecentTurnCount        *int
	RetainedMessageTokenBudget *int
	MinRawTailSteps            *int
	Telemetry                  any
	EventTags                  []string
	Extra                      map[string]any
}

// ExperienceTrajectoryOptions controls trajectory pagination and date filters.
type ExperienceTrajectoryOptions struct {
	Limit     *int
	Offset    *int
	StartDate string
	EndDate   string
}

// ExperienceOutcomeOptions controls outcome date filters.
type ExperienceOutcomeOptions struct {
	StartDate string
	EndDate   string
}

// ResolveAssetsOptions controls OpenViking Assets manifest resolution.
type ResolveAssetsOptions struct {
	CatalogYAML   string
	ManifestLabel string
	CatalogLabel  string
	Extra         map[string]any
}

// AssetGitAuth is one-shot Git authentication for asset preflight.
type AssetGitAuth struct {
	Username string `json:"username,omitempty"`
	Token    string `json:"token,omitempty"`
}

// PreflightAssetOptions controls Git asset access checks.
type PreflightAssetOptions struct {
	Branch     string
	Commit     string
	AuthConfig *AssetGitAuth
	Extra      map[string]any
}

// ListTasksOptions controls ListTasks.
type ListTasksOptions struct {
	TaskType   string
	Status     string
	ResourceID string
	Limit      int
}

// PackOptions controls ovpack export/backup.
type PackOptions struct {
	IncludeVectors bool
}

// ImportPackOptions controls ovpack import/restore.
type ImportPackOptions struct {
	OnConflict string
	VectorMode string
}

// AdminMigrateOptions controls AdminMigrate.
type AdminMigrateOptions struct {
	Cleanup bool
}

// FindResult is the structured retrieval response returned by Find and Search.
type FindResult struct {
	Memories     []MatchedContext `json:"memories,omitempty"`
	Resources    []MatchedContext `json:"resources,omitempty"`
	Skills       []MatchedContext `json:"skills,omitempty"`
	QueryPlan    *QueryPlan       `json:"query_plan,omitempty"`
	QueryResults []map[string]any `json:"query_results,omitempty"`
	Total        int              `json:"total,omitempty"`
}

// SearchContextEntry is one assembled context entry.
type SearchContextEntry struct {
	URI      string  `json:"uri,omitempty"`
	Category string  `json:"category,omitempty"`
	Score    float64 `json:"score,omitempty"`
	Detail   string  `json:"detail,omitempty"`
	Text     string  `json:"text,omitempty"`
	Origin   string  `json:"origin,omitempty"`
}

// SearchContextResult is an injection-ready context response.
type SearchContextResult struct {
	Entries  []SearchContextEntry `json:"entries,omitempty"`
	Rendered string               `json:"rendered,omitempty"`
	Digest   string               `json:"digest,omitempty"`
	Stats    map[string]any       `json:"stats,omitempty"`
}

// MatchedContext is one retrieval hit. Only the fields the retrieval pipeline
// actually populates are exposed; search_tags is surfaced under the "tags" key
// to match the tags filter parameter accepted by Find and Search.
type MatchedContext struct {
	URI         string   `json:"uri,omitempty"`
	ContextType string   `json:"context_type,omitempty"`
	Level       int      `json:"level,omitempty"`
	Abstract    string   `json:"abstract,omitempty"`
	Content     string   `json:"content,omitempty"`
	Overview    string   `json:"overview,omitempty"`
	Category    string   `json:"category,omitempty"`
	Score       float64  `json:"score,omitempty"`
	MatchReason string   `json:"match_reason,omitempty"`
	Tags        []string `json:"tags,omitempty"`
}

// QueryPlan describes search query expansion details when the server returns them.
type QueryPlan struct {
	Queries []TypedQuery   `json:"queries,omitempty"`
	Raw     map[string]any `json:"-"`
}

// TypedQuery is a query generated for a specific context type.
type TypedQuery struct {
	Query             string   `json:"query,omitempty"`
	ContextType       string   `json:"context_type,omitempty"`
	Intent            string   `json:"intent,omitempty"`
	Priority          int      `json:"priority,omitempty"`
	TargetDirectories []string `json:"target_directories,omitempty"`
}
