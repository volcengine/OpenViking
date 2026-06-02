Set-ExecutionPolicy -ExecutionPolicy Bypass -Scope Process -Force

# select-embedding.ps1  v1.1
# Selects embedding provider: Ollama (local) first, Jina cloud as fallback.
# Called automatically by restart-openviking.ps1 and openviking-watchdog.py.
#
# To use Ollama: install from https://ollama.ai, then: ollama pull nomic-embed-text
# To use Jina: get a free API key from https://jina.ai

$OV_CONF = "$env:USERPROFILE\.openviking\ov.conf"

$JINA_EMBED = @{
    provider       = "jina"
    api_key        = $env:JINA_API_KEY    # set JINA_API_KEY as environment variable
    api_base       = "https://api.jina.ai/v1"
    model          = "jina-embeddings-v3"
    dimension      = 768
    query_param    = "retrieval.query"
    document_param = "retrieval.passage"
}

$OLLAMA_EMBED = @{
    provider  = "openai"    # Ollama uses OpenAI-compatible API
    api_key   = "ollama"
    api_base  = "http://localhost:11434/v1"
    model     = "nomic-embed-text"
    dimension = 768
}

# Check if Ollama is running with nomic-embed-text available
$ollamaUp = $false
try {
    $r = Invoke-WebRequest -Uri "http://localhost:11434/api/tags" `
         -TimeoutSec 2 -UseBasicParsing -ErrorAction Stop
    if ($r.StatusCode -eq 200) {
        $tags = $r.Content | ConvertFrom-Json
        foreach ($m in $tags.models) {
            if ($m.name -like "*nomic-embed-text*") { $ollamaUp = $true; break }
        }
    }
} catch {}

$conf = Get-Content $OV_CONF -Raw | ConvertFrom-Json

if ($ollamaUp) {
    Write-Host "  [Embedding] Ollama detected -> nomic-embed-text (local)" -ForegroundColor Cyan
    $embed = $OLLAMA_EMBED
    $conf.embedding.dense.provider  = $embed.provider
    $conf.embedding.dense.api_key   = $embed.api_key
    $conf.embedding.dense.api_base  = $embed.api_base
    $conf.embedding.dense.model     = $embed.model
    $conf.embedding.dense.dimension = $embed.dimension
    # Remove Jina-specific params not valid for OpenAI-compatible provider
    $denseProps = $conf.embedding.dense.PSObject.Properties.Name
    if ($denseProps -contains "query_param")    { $conf.embedding.dense.PSObject.Properties.Remove("query_param") }
    if ($denseProps -contains "document_param") { $conf.embedding.dense.PSObject.Properties.Remove("document_param") }
} else {
    Write-Host "  [Embedding] Ollama not available -> Jina cloud (jina-embeddings-v3)" -ForegroundColor Cyan
    $embed = $JINA_EMBED
    $conf.embedding.dense.provider  = $embed.provider
    $conf.embedding.dense.api_key   = $embed.api_key
    $conf.embedding.dense.api_base  = $embed.api_base
    $conf.embedding.dense.model     = $embed.model
    $conf.embedding.dense.dimension = $embed.dimension
    $conf.embedding.dense | Add-Member -NotePropertyName query_param    -NotePropertyValue $embed.query_param    -Force
    $conf.embedding.dense | Add-Member -NotePropertyName document_param -NotePropertyValue $embed.document_param -Force
}

$conf | ConvertTo-Json -Depth 10 | Set-Content $OV_CONF -Encoding UTF8
Write-Host "  [Embedding] ov.conf updated" -ForegroundColor Green
