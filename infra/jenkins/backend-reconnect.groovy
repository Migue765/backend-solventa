import jenkins.model.Jenkins
import jenkins.util.Timer
import java.util.concurrent.TimeUnit

// Backend-only companion; does not replace Android/web reconnection settings.
Timer.get().scheduleWithFixedDelay({
    def j = Jenkins.get()
    def computer = j.getComputer('android-aws')
    if (computer == null || !computer.offline || computer.temporarilyOffline || computer.connecting) return
    if (j.queue.items.any { item ->
        def path = new URI(item.task.url ?: '').path
        if (!path.startsWith('/')) path = '/' + path
        path.startsWith('/job/solventa-backend/job/pr-checks/')
    }) computer.connect(false)
} as Runnable, 30, 60, TimeUnit.SECONDS)
