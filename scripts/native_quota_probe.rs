//! Isolated provider and a single in-memory engine owner. This never opens a desktop thread.
use std::sync::Arc;
use codex_core::{NotSubmittedReason, TurnInputRequest, TurnInputSubmission};
use codex_extension_api::ExtensionRegistryBuilder;
use codex_protocol::protocol::{CodexErrorInfo, EventMsg};
use codex_protocol::user_input::UserInput;
use core_test_support::{ThreadIdle, responses, test_codex::test_codex, wait_for_event};
use serde_json::json;
use wiremock::ResponseTemplate;

#[tokio::test]
async fn quota_failure_can_wake_once_on_same_owner() -> anyhow::Result<()> {
    let server = responses::start_mock_server().await;
    let mut extensions = ExtensionRegistryBuilder::new();
    extensions.thread_lifecycle_contributor(Arc::new(ThreadIdle));
    let test = test_codex()
        .with_extensions(Arc::new(extensions.build()))
        .with_config(|config| {
            config.model_provider.request_max_retries = Some(0);
            config.model_provider.stream_max_retries = Some(0);
        })
        .build_with_auto_env(&server).await?;
    let blocked = ResponseTemplate::new(429)
        .insert_header("x-codex-rate-limit-reached-type", "rate_limit_reached")
        .set_body_json(json!({"error":{"type":"usage_limit_reached","resets_at":1800000000}}));
    let accepted = responses::sse_response(responses::sse(vec![
        responses::ev_response_created("after-reset"),
        responses::ev_assistant_message("answer", "recovered"),
        responses::ev_completed("after-reset"),
    ]));
    let mock = responses::mount_response_sequence(&server, vec![blocked, accepted]).await;
    let input = UserInput::Text { text: "Reply with recovered.".to_string(), text_elements: vec![] };
    let TurnInputSubmission::Started { turn_id: original, .. } =
        test.codex.start_or_steer_turn(TurnInputRequest::user_input(vec![input])).await?
    else { anyhow::bail!("original turn did not start") };
    let EventMsg::TurnComplete(failed) = wait_for_event(&test.codex, |e| matches!(e, EventMsg::TurnComplete(_))).await
    else { unreachable!() };
    assert_eq!(failed.turn_id, original);
    assert_eq!(failed.error.and_then(|e| e.codex_error_info), Some(CodexErrorInfo::UsageLimitExceeded));
    ThreadIdle::wait(&test.codex).await;
    assert_eq!(mock.requests().len(), 1);

    let TurnInputSubmission::Started { turn_id, .. } = test.codex
        .continue_turn_if_idle(TurnInputRequest::user_input(vec![]), original.clone()).await?
    else { anyhow::bail!("empty wake on the failed turn did not start") };
    let EventMsg::TurnComplete(done) = wait_for_event(&test.codex, |e| {
        assert!(!matches!(e, EventMsg::UserMessage(_)));
        matches!(e, EventMsg::TurnComplete(_))
    }).await else { unreachable!() };
    assert_ne!(turn_id, original);
    assert_eq!(done.turn_id, turn_id);
    assert!(done.error.is_none());
    assert_eq!(done.last_agent_message.as_deref(), Some("recovered"));
    ThreadIdle::wait(&test.codex).await;
    assert_eq!(test.codex.continue_turn_if_idle(TurnInputRequest::user_input(vec![]), original).await?,
        TurnInputSubmission::NotSubmitted { reason: NotSubmittedReason::Superseded });
    assert_eq!(mock.requests().len(), 2);
    test.codex.shutdown_and_wait().await?;
    Ok(())
}
