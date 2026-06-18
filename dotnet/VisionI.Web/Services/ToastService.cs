namespace VisionI.Web.Services;

public enum ToastLevel { Info, Success, Warning, Error }

public sealed record ToastMessage(string Id, string Text, ToastLevel Level, DateTime ExpiresAt);

/// <summary>
/// Lightweight scoped service. Pages call Toast.Show(...) instead of Console.Error.WriteLine.
/// The ToastNotification component subscribes to OnChanged and renders the queue.
/// </summary>
public sealed class ToastService
{
    private readonly List<ToastMessage> _queue = new();
    public IReadOnlyList<ToastMessage> Queue => _queue;

    public event Action? OnChanged;

    public void Show(string text, ToastLevel level = ToastLevel.Info, int durationMs = 4000)
    {
        var msg = new ToastMessage(
            Guid.NewGuid().ToString("N")[..8],
            text,
            level,
            DateTime.UtcNow.AddMilliseconds(durationMs));

        _queue.Add(msg);
        OnChanged?.Invoke();

        // Auto-dismiss
        _ = Task.Delay(durationMs + 300).ContinueWith(_ =>
        {
            _queue.Remove(msg);
            OnChanged?.Invoke();
        });
    }

    public void Dismiss(string id)
    {
        _queue.RemoveAll(m => m.Id == id);
        OnChanged?.Invoke();
    }

    public void ShowError(string text) => Show(text, ToastLevel.Error, 6000);
    public void ShowSuccess(string text) => Show(text, ToastLevel.Success, 3000);
    public void ShowWarning(string text) => Show(text, ToastLevel.Warning, 5000);
}
