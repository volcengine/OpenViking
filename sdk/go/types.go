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
	Args                map[string]any
	Telemetry           any
}

// AddSkillOptions controls AddSkill.
type AddSkillOptions struct {
	Wait      bool
	Timeout   *float64
	Telemetry any
	// TargetURI scopes the operation to a skills root such as
	// "viking://agent/skills" (account-shared) or a per-user root. A nil
	// value omits target_uri and lets the server use its default root.
	TargetURI any
}

// AdminCreateAccountOptions controls AdminCreateAccountWithOptions.
type AdminCreateAccountOptions struct {
	UserConfig map[string]any
}

// AdminRegisterUserOptions controls AdminRegisterUserWithOptions.
type AdminRegisterUserOptions struct {
	UserConfig map[string]any
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
}

// TreeOptions controls Tree.
type TreeOptions struct {
	Output        string
	AbsLimit      int
	ShowAllHidden bool
	NodeLimit     int
}

// RemoveOptions controls Remove.
type RemoveOptions struct {
	Recursive bool
	Wait      bool
	Timeout   *float64
}

// WriteOptions controls Write.
type WriteOptions struct {
	Mode      string
	Wait      bool
	Timeout   *float64
	Telemetry any
}

// SetTagsOptions controls SetTags.
type SetTagsOptions struct {
	Mode      string
	Recursive bool
	Telemetry any
}

// ReindexOptions controls Reindex.
type ReindexOptions struct {
	Mode string
	Wait bool
}

// FindOptions controls Find.
type FindOptions struct {
	TargetURI      any
	Limit          int
	NodeLimit      *int
	ScoreThreshold *float64
	Filter         map[string]any
	ContextType    any
	Telemetry      any
	Since          string
	Until          string
	TimeField      string
	Level          []int
}

// SearchOptions controls Search.
type SearchOptions struct {
	TargetURI      any
	SessionID      string
	Limit          int
	NodeLimit      *int
	ScoreThreshold *float64
	Filter         map[string]any
	ContextType    any
	Telemetry      any
	Since          string
	Until          string
	TimeField      string
	Level          []int
}

// GrepOptions controls Grep.
type GrepOptions struct {
	CaseInsensitive bool
	NodeLimit       *int
	ExcludeURI      string
}

// CreateSessionOptions controls CreateSession.
type CreateSessionOptions struct {
	SessionID    string
	MemoryPolicy map[string]any
	Telemetry    any
}

// GetSessionOptions controls GetSession.
type GetSessionOptions struct {
	AutoCreate bool
}

// AddMessageOptions controls AddMessage.
type AddMessageOptions struct {
	Content   *string
	Parts     []map[string]any
	CreatedAt string
	PeerID    string
	Telemetry any
}

// Message is one session message payload for BatchAddMessages.
type Message struct {
	Role      string           `json:"role"`
	Content   *string          `json:"content,omitempty"`
	Parts     []map[string]any `json:"parts,omitempty"`
	CreatedAt string           `json:"created_at,omitempty"`
	PeerID    string           `json:"peer_id,omitempty"`
}

// BatchAddMessagesOptions controls BatchAddMessages.
type BatchAddMessagesOptions struct {
	Telemetry any
}

// CommitSessionOptions controls CommitSession.
type CommitSessionOptions struct {
	KeepRecentCount int
	Telemetry       any
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

// MatchedContext is one retrieval hit.
type MatchedContext struct {
	URI         string           `json:"uri,omitempty"`
	ContextType string           `json:"context_type,omitempty"`
	Level       int              `json:"level,omitempty"`
	Abstract    string           `json:"abstract,omitempty"`
	Overview    string           `json:"overview,omitempty"`
	Category    string           `json:"category,omitempty"`
	Score       float64          `json:"score,omitempty"`
	MatchReason string           `json:"match_reason,omitempty"`
	Relations   []RelatedContext `json:"relations,omitempty"`
}

// RelatedContext is a related context reference attached to a retrieval hit.
type RelatedContext struct {
	URI        string  `json:"uri,omitempty"`
	Reason     string  `json:"reason,omitempty"`
	Score      float64 `json:"score,omitempty"`
	Relation   string  `json:"relation,omitempty"`
	RelationID string  `json:"relation_id,omitempty"`
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
