output "function_app" {
  value = azurerm_linux_function_app.agent.name
}

output "openai_endpoint" {
  value = azurerm_cognitive_account.openai.endpoint
}

output "key_vault_url" {
  value = azurerm_key_vault.kv.vault_uri
}
