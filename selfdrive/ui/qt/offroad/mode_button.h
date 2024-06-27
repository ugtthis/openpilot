#pragma once

#include <QWidget>
#include <QPushButton>
#include <QLabel>
#include <QTimer>

class ModeButton : public QWidget {
  Q_OBJECT

public:
  explicit ModeButton(QWidget* parent = nullptr);
  void setModeText(const QString &text);
  void setDescriptionText(const QString &text);
  void setGradient(const QString &gradient);
  void toggleMode();
  void expandDescription();
  void collapseDescription();

signals:
  void modeToggled(const QString &mode, bool enabled);

private slots:
  void onButtonClicked();
  void onToggleClicked();
  void autoCollapse();

private:
  QLabel *headerText;
  QLabel *descriptionText;
  QPushButton *toggleButton;
  QTimer *collapseTimer;
  bool isExpanded;
  bool isToggledOn;
  QString modeText;
  QString description;

protected:
  void paintEvent(QPaintEvent *event) override;
};
