user = User.find_by_username!('root')
name = 'auditor-live-bootstrap'
token = user.personal_access_tokens.find_by(name: name)
unless token
  token = user.personal_access_tokens.build(name: name, scopes: ['api', 'read_repository', 'write_repository'], expires_at: 14.days.from_now)
  token.set_token(File.read('/run/live-secrets/gitlab-token').strip)
  token.save!
end
ApplicationSetting.current.update!(allow_local_requests_from_web_hooks_and_services: true,
                                   allow_local_requests_from_system_hooks: true)
puts({user_id: user.id, token_record_id: token.id, local_webhooks: true}.to_json)
