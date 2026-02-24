use crate::client::HttpClient;
use crate::error::Result;
use crate::output::{output_success, OutputFormat};
use serde_json::json;

pub async fn create_account(
    client: &HttpClient,
    account_id: &str,
    admin_user_id: &str,
    output_format: OutputFormat,
    compact: bool,
) -> Result<()> {
    let response = client.admin_create_account(account_id, admin_user_id).await?;
    output_success(&response, output_format, compact);
    Ok(())
}

pub async fn list_accounts(
    client: &HttpClient,
    output_format: OutputFormat,
    compact: bool,
) -> Result<()> {
    let response = client.admin_list_accounts().await?;
    output_success(&response, output_format, compact);
    Ok(())
}

pub async fn delete_account(
    client: &HttpClient,
    account_id: &str,
    output_format: OutputFormat,
    compact: bool,
) -> Result<()> {
    let response = client.admin_delete_account(account_id).await?;
    let result = if response.is_null()
        || response.as_object().map(|o| o.is_empty()).unwrap_or(false)
    {
        json!({"account_id": account_id})
    } else {
        response
    };
    output_success(&result, output_format, compact);
    Ok(())
}

pub async fn register_user(
    client: &HttpClient,
    account_id: &str,
    user_id: &str,
    role: &str,
    output_format: OutputFormat,
    compact: bool,
) -> Result<()> {
    let response = client.admin_register_user(account_id, user_id, role).await?;
    output_success(&response, output_format, compact);
    Ok(())
}

pub async fn list_users(
    client: &HttpClient,
    account_id: &str,
    output_format: OutputFormat,
    compact: bool,
) -> Result<()> {
    let response = client.admin_list_users(account_id).await?;
    output_success(&response, output_format, compact);
    Ok(())
}

pub async fn remove_user(
    client: &HttpClient,
    account_id: &str,
    user_id: &str,
    output_format: OutputFormat,
    compact: bool,
) -> Result<()> {
    let response = client.admin_remove_user(account_id, user_id).await?;
    let result = if response.is_null()
        || response.as_object().map(|o| o.is_empty()).unwrap_or(false)
    {
        json!({"account_id": account_id, "user_id": user_id})
    } else {
        response
    };
    output_success(&result, output_format, compact);
    Ok(())
}

pub async fn set_role(
    client: &HttpClient,
    account_id: &str,
    user_id: &str,
    role: &str,
    output_format: OutputFormat,
    compact: bool,
) -> Result<()> {
    let response = client.admin_set_role(account_id, user_id, role).await?;
    output_success(&response, output_format, compact);
    Ok(())
}

pub async fn regenerate_key(
    client: &HttpClient,
    account_id: &str,
    user_id: &str,
    output_format: OutputFormat,
    compact: bool,
) -> Result<()> {
    let response = client.admin_regenerate_key(account_id, user_id).await?;
    output_success(&response, output_format, compact);
    Ok(())
}
